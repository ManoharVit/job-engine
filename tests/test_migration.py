import pytest
import sqlite3
import json
from pathlib import Path
from src.review.legacy_import import run_migration

def make_test_db(path: Path, use_jobs_schema: bool = False):
    con = sqlite3.connect(path)
    if not use_jobs_schema:
        con.execute("""CREATE TABLE applications (
            id INTEGER PRIMARY KEY, platform TEXT, job_url TEXT UNIQUE, title TEXT, company TEXT,
            location TEXT, match_score REAL, status TEXT DEFAULT 'pending', error_message TEXT,
            applied_at TIMESTAMP, created_at TIMESTAMP)""")
        rows = [
            ("naukri", "url1", "t", "c", "l", 0.8, "applied"),
            ("linkedin", "url2", "t2", "c2", "l2", 0.5, "applied"),
            ("linkedin", "url3", "t3", "c3", "l3", 0.5, "pending")
        ]
        con.executemany("INSERT INTO applications (platform, job_url, title, company, location, match_score, status) VALUES (?,?,?,?,?,?,?)", rows)
    else:
        con.execute("""CREATE TABLE jobs (
            id INTEGER PRIMARY KEY, source TEXT, source_job_id TEXT, canonical_url TEXT, title TEXT,
            company TEXT, application_status TEXT)""")
        con.execute("""CREATE TABLE submission_evidence (
            id INTEGER PRIMARY KEY, job_id INTEGER, evidence_id TEXT)""")
        rows = [
            ("s", "1", "u", "t", "c", "APPLIED"), # false positive (no evidence)
            ("s", "2", "u2", "t", "c", "APPLIED"), # true positive (has evidence)
            ("s", "3", "u3", "t", "c", "DISCOVERED") # not applied
        ]
        con.executemany("INSERT INTO jobs (source, source_job_id, canonical_url, title, company, application_status) VALUES (?,?,?,?,?,?)", rows)
        con.execute("INSERT INTO submission_evidence (job_id, evidence_id) VALUES (2, 'e1')")
    con.commit()
    con.close()

def test_dry_run_non_mutation(tmp_path):
    db_path = tmp_path / "test.db"
    make_test_db(db_path)
    report_path = tmp_path / "report.json"
    
    run_migration(db_path, dry_run=True, backup_dir=tmp_path / "backups", report_path=report_path)
    
    # Check that db wasn't mutated
    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    cursor.execute("SELECT status FROM applications WHERE id = 1")
    assert cursor.fetchone()[0] == "applied"
    con.close()
    
    # Check report
    report = json.loads(report_path.read_text())
    assert len(report) == 2
    assert report[0]["new_status"] == "SUBMISSION_UNCONFIRMED"

def test_false_positive_conversion_and_backup(tmp_path):
    db_path = tmp_path / "test.db"
    make_test_db(db_path)
    report_path = tmp_path / "report.json"
    backup_dir = tmp_path / "backups"
    
    run_migration(db_path, dry_run=False, backup_dir=backup_dir, report_path=report_path, auto_confirm=True)
    
    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    cursor.execute("SELECT status FROM applications WHERE id = 1")
    assert cursor.fetchone()[0] == "SUBMISSION_UNCONFIRMED"
    cursor.execute("SELECT status FROM applications WHERE id = 3")
    assert cursor.fetchone()[0] == "pending"
    con.close()
    
    # Check backup exists
    backups = list(backup_dir.glob("*.db"))
    assert len(backups) == 1
    
    # Verify backup is untouched
    b_con = sqlite3.connect(backups[0])
    b_cur = b_con.cursor()
    b_cur.execute("SELECT status FROM applications WHERE id = 1")
    assert b_cur.fetchone()[0] == "applied"
    b_con.close()

def test_idempotence(tmp_path):
    db_path = tmp_path / "test.db"
    make_test_db(db_path)
    report_path = tmp_path / "report.json"
    backup_dir = tmp_path / "backups"
    
    # First run
    run_migration(db_path, dry_run=False, backup_dir=backup_dir, report_path=report_path, auto_confirm=True)
    
    # Second run
    run_migration(db_path, dry_run=False, backup_dir=backup_dir, report_path=report_path, auto_confirm=True)
    
    # Report should have 0 candidates eligible
    report = json.loads(report_path.read_text())
    assert len(report) == 0

def test_rollback_on_error(tmp_path):
    db_path = tmp_path / "test.db"
    make_test_db(db_path)
    
    # Add a trigger to force an error on UPDATE
    con = sqlite3.connect(db_path)
    con.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON applications BEGIN SELECT RAISE(ABORT, 'Mocked failure'); END;")
    con.commit()
    con.close()
    
    with pytest.raises(RuntimeError, match="Transaction failed"):
        run_migration(db_path, dry_run=False, backup_dir=tmp_path / "backups", report_path=tmp_path / "report.json", auto_confirm=True)
        
    # Verify no rows were changed
    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    cursor.execute("SELECT status FROM applications WHERE id = 1")
    assert cursor.fetchone()[0] == "applied"
    con.close()

def test_jobs_schema_selection(tmp_path):
    db_path = tmp_path / "test.db"
    make_test_db(db_path, use_jobs_schema=True)
    report_path = tmp_path / "report.json"
    
    run_migration(db_path, dry_run=False, backup_dir=tmp_path / "backups", report_path=report_path, auto_confirm=True)
    
    con = sqlite3.connect(db_path)
    cursor = con.cursor()
    cursor.execute("SELECT application_status FROM jobs ORDER BY id")
    statuses = [r[0] for r in cursor.fetchall()]
    assert statuses == ["SUBMISSION_UNCONFIRMED", "APPLIED", "DISCOVERED"]
    con.close()
