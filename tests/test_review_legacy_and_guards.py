"""Legacy tracker import (read-only) and structural safety guards."""
import ast
import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.review.legacy_import import LegacyImportRecord, read_legacy_tracker, summarize
from src.review.states import ReviewState

SRC = Path(__file__).resolve().parents[1] / "src"


def make_legacy_db(path: Path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE applications (
        id INTEGER PRIMARY KEY, platform TEXT, job_url TEXT UNIQUE, title TEXT, company TEXT,
        location TEXT, match_score REAL, status TEXT DEFAULT 'pending', error_message TEXT,
        applied_at TIMESTAMP, created_at TIMESTAMP)""")
    rows = [
        ("naukri", "https://www.naukri.com/job-listings-a-1", "Python Developer", "A", "Pune", 0.8, "applied"),
        ("linkedin", "https://www.linkedin.com/jobs/view/2", "Robotics Engineer", "B", "Bengaluru", 0.5, "applied"),
        ("naukri", "https://www.naukri.com/job-listings-c-3", "Backend Developer", "C", "Chennai", 0.7, "pending"),
        ("naukri", "https://www.naukri.com/job-listings-d-4", "Senior Dev", "D", "Hyderabad", 0.2, "skipped"),
        ("linkedin", "https://www.linkedin.com/jobs/view/5", "Data Engineer", "E", "Pune", 0.6, "failed"),
        ("linkedin", "https://www.linkedin.com/jobs/view/6", "ML Engineer", "F", "Pune", 0.6, "weird"),
    ]
    con.executemany("INSERT INTO applications (platform, job_url, title, company, location, match_score, status) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_applied_rows_become_unconfirmed(tmp_path):
    db = tmp_path / "tracker_copy.db"
    make_legacy_db(db)
    recs = read_legacy_tracker(db)
    by_title = {r.title: r for r in recs}
    for title in ("Python Developer", "Robotics Engineer"):
        assert by_title[title].mapped_state == ReviewState.SUBMISSION_UNCONFIRMED
        assert by_title[title].submission_claim == "unconfirmed"
        assert "legacy_applied_without_evidence" in by_title[title].reasons
    assert by_title["Backend Developer"].mapped_state == ReviewState.DISCOVERED
    assert by_title["Senior Dev"].mapped_state == ReviewState.SKIPPED
    assert by_title["Data Engineer"].mapped_state == ReviewState.SUBMISSION_UNCONFIRMED
    assert "legacy_failed_outcome_unknown" in by_title["Data Engineer"].reasons
    assert by_title["ML Engineer"].mapped_state == ReviewState.REVIEW_REQUIRED
    assert all(r.mapped_state != ReviewState.CONFIRMED_BY_HUMAN_REVIEW for r in recs)


def test_legacy_import_never_writes_the_source_file(tmp_path):
    db = tmp_path / "tracker_copy.db"
    make_legacy_db(db)
    before = sha(db)
    read_legacy_tracker(db)
    assert sha(db) == before
    assert not (tmp_path / "tracker_copy.db-journal").exists()


def test_legacy_records_store_hashes_not_urls(tmp_path):
    db = tmp_path / "tracker_copy.db"
    make_legacy_db(db)
    for r in read_legacy_tracker(db):
        dumped = r.model_dump_json()
        assert "http" not in dumped
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", r.job_ref_hash)
    with pytest.raises(ValidationError):
        LegacyImportRecord(platform="naukri", job_ref_hash="sha256:" + "0" * 64, title="t", company="c",
                           legacy_status="applied", mapped_state=ReviewState.SUBMISSION_UNCONFIRMED,
                           reasons=(), submission_claim="confirmed_by_human_review")


def test_legacy_summary_counts(tmp_path):
    db = tmp_path / "tracker_copy.db"
    make_legacy_db(db)
    s = summarize(read_legacy_tracker(db))
    assert s["by_mapped_state"]["SUBMISSION_UNCONFIRMED"] == 3
    assert s["confirmed_by_human_review"] == 0
    assert s["legacy_applied_rows"] == 2


def test_legacy_import_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_legacy_tracker(tmp_path / "nope.db")


# ---------- Structural guards over src/ ----------

FORBIDDEN_MODULES = {"playwright", "selenium", "webdriver", "pyppeteer", "requests", "httpx",
                     "aiohttp", "urllib.request", "urllib3", "websocket", "websockets", "socket"}


def iter_src_files():
    return [p for p in SRC.rglob("*.py") if "egg-info" not in str(p)]


def test_no_browser_or_network_imports_in_src():
    offenders = []
    for path in iter_src_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                if n in FORBIDDEN_MODULES or n.split(".")[0] in FORBIDDEN_MODULES - {"urllib.request"}:
                    offenders.append(f"{path.relative_to(SRC)}: {n}")
    assert offenders == []


def test_no_applied_successfully_literal_in_src():
    pattern = re.compile(r"applied\s+successfully", re.IGNORECASE)
    offenders = [str(p.relative_to(SRC)) for p in iter_src_files() if pattern.search(p.read_text())]
    assert offenders == []


def test_form_filling_backend_removed():
    assert not (SRC / "api" / "extension" / "field_matcher.py").exists()
    assert not (SRC / "api" / "extension_router.py").exists()
