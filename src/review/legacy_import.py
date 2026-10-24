import os
import sqlite3
import shutil
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Dict, Any
from pydantic import BaseModel, model_validator
from src.review.states import ReviewState
from src.review.logs import job_ref_hash

class LegacyImportRecord(BaseModel):
    platform: str
    job_ref_hash: str
    title: str
    company: str
    legacy_status: str
    mapped_state: ReviewState
    reasons: Tuple[str, ...] = ()
    submission_claim: str = "none"

    @model_validator(mode="after")
    def validate_rules(self) -> "LegacyImportRecord":
        if not self.job_ref_hash.startswith("sha256:") or len(self.job_ref_hash) != 71:
            raise ValueError("hash must start with sha256:")
            
        if self.submission_claim == "confirmed_by_human_review":
            if self.mapped_state != ReviewState.CONFIRMED_BY_HUMAN_REVIEW:
                raise ValueError("Mismatch state for confirmed claim")

        if self.mapped_state == ReviewState.CONFIRMED_BY_HUMAN_REVIEW:
            if self.submission_claim != "confirmed_by_human_review":
                 raise ValueError("Mismatch claim for confirmed state")

        return self

def read_legacy_tracker(db_path: Path) -> List[LegacyImportRecord]:
    if not db_path.exists():
        raise FileNotFoundError(f"Database {db_path} not found")
        
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cursor = con.cursor()
    cursor.execute("SELECT platform, job_url, title, company, status FROM applications")
    
    records = []
    for row in cursor.fetchall():
        status = row["status"]
        if status == "applied":
            mapped_state = ReviewState.SUBMISSION_UNCONFIRMED
            reasons = ("legacy_applied_without_evidence",)
            submission_claim = "unconfirmed"
        elif status == "pending":
            mapped_state = ReviewState.DISCOVERED
            reasons = ()
            submission_claim = "none"
        elif status == "skipped":
            mapped_state = ReviewState.SKIPPED
            reasons = ()
            submission_claim = "none"
        elif status == "failed":
            mapped_state = ReviewState.SUBMISSION_UNCONFIRMED
            reasons = ("legacy_failed_outcome_unknown",)
            submission_claim = "unconfirmed"
        else:
            mapped_state = ReviewState.REVIEW_REQUIRED
            reasons = ()
            submission_claim = "none"
            
        r = LegacyImportRecord(
            platform=row["platform"],
            job_ref_hash=job_ref_hash(row["job_url"]),
            title=row["title"],
            company=row["company"],
            legacy_status=status,
            mapped_state=mapped_state,
            reasons=reasons,
            submission_claim=submission_claim
        )
        records.append(r)
        
    con.close()
    return records

def summarize(records: List[LegacyImportRecord]) -> Dict[str, Any]:
    counts = {}
    legacy_applied = 0
    confirmed = 0
    
    for r in records:
        s = r.mapped_state.value
        counts[s] = counts.get(s, 0) + 1
        
        if r.legacy_status == "applied":
            legacy_applied += 1
            
        if r.submission_claim == "confirmed_by_human_review":
            confirmed += 1
            
    return {
        "by_mapped_state": counts,
        "confirmed_by_human_review": confirmed,
        "legacy_applied_rows": legacy_applied
    }

def check_db_integrity(con: sqlite3.Connection):
    cursor = con.cursor()
    cursor.execute("PRAGMA integrity_check;")
    result = cursor.fetchone()
    if not result or result[0] != "ok":
        raise ValueError(f"Database integrity check failed: {result}")

def run_migration(db_path: Path, dry_run: bool, backup_dir: Path | None, report_path: Path | None, auto_confirm: bool = False):
    if not db_path.exists():
        raise FileNotFoundError(f"Database {db_path} not found")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    check_db_integrity(con)

    cursor = con.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='applications';")
    has_applications = cursor.fetchone() is not None
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs';")
    has_jobs = cursor.fetchone() is not None

    candidate_count = 0
    eligible_count = 0
    report_data = []

    if has_applications:
        cursor.execute("SELECT id, status FROM applications WHERE status = 'applied'")
        rows = cursor.fetchall()
        candidate_count = len(rows)
        eligible_ids = []
        for row in rows:
            # Idempotency check: if already unconfirmed, don't update (though query filters for 'applied')
            eligible_ids.append(row['id'])
            report_data.append({
                "id": row["id"],
                "old_status": row["status"],
                "new_status": "SUBMISSION_UNCONFIRMED",
                "reason": "legacy_applied_without_evidence"
            })
        eligible_count = len(eligible_ids)
        update_query = "UPDATE applications SET status = 'SUBMISSION_UNCONFIRMED' WHERE id = ?"
    elif has_jobs:
        cursor.execute("SELECT id, application_status FROM jobs WHERE application_status = 'APPLIED'")
        rows = cursor.fetchall()
        candidate_count = len(rows)
        eligible_ids = []
        # Check for evidence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='submission_evidence';")
        has_evidence_table = cursor.fetchone() is not None
        
        for row in rows:
            has_evidence = False
            if has_evidence_table:
                cursor.execute("SELECT id FROM submission_evidence WHERE job_id = ?", (row["id"],))
                has_evidence = cursor.fetchone() is not None
            
            if not has_evidence:
                eligible_ids.append(row["id"])
                report_data.append({
                    "id": row["id"],
                    "old_status": row["application_status"],
                    "new_status": "SUBMISSION_UNCONFIRMED",
                    "reason": "missing_submission_evidence"
                })
        eligible_count = len(eligible_ids)
        update_query = "UPDATE jobs SET application_status = 'SUBMISSION_UNCONFIRMED' WHERE id = ?"
    else:
        con.close()
        raise ValueError("Database has neither 'applications' nor 'jobs' table.")

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report_data, f, indent=2)

    print("Dry run complete." if dry_run else "Migration prepared.")
    print(f"Database: {db_path.name}")
    print(f"Candidate records found: {candidate_count}")
    print(f"Records eligible for correction: {eligible_count}")
    
    if dry_run:
        print("Original database: untouched")
        print("Proposed new status: SUBMISSION_UNCONFIRMED")
        if backup_dir:
            backup_path = backup_dir / f"{db_path.stem}-YYYYMMDD-HHMMSS.db"
            print(f"Backup to be created on apply: {backup_path}")
        if report_path:
            print(f"Report: {report_path}")
        con.close()
        return

    if backup_dir:
        backup_path = backup_dir / f"{db_path.stem}-YYYYMMDD-HHMMSS.db"
        print(f"Backup to be created on apply: {backup_path}")

    if report_path:
        print(f"Report: {report_path}")

    if not auto_confirm and eligible_count > 0:
        ans = input(f"\nApply these {eligible_count} local database corrections and create the stated backup? [y/N]: ")
        if ans.lower() not in ('y', 'yes'):
            print("Aborted by user.")
            con.close()
            return

    if eligible_count == 0:
        print("\nNo records to correct. Skipping apply.")
        con.close()
        return

    if not backup_dir:
        backup_dir = Path("output/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    actual_backup_path = backup_dir / f"{db_path.stem}-{timestamp}.db"
    
    backup_con = sqlite3.connect(actual_backup_path)
    con.backup(backup_con)
    backup_con.close()

    try:
        cursor.execute("BEGIN TRANSACTION")
        for eid in eligible_ids:
            cursor.execute(update_query, (eid,))
        con.commit()
    except Exception as e:
        con.rollback()
        con.close()
        raise RuntimeError(f"Transaction failed, rolled back: {e}")
        
    check_db_integrity(con)
    con.close()
    
    print("Original database: modified")
    print(f"Applied new status: SUBMISSION_UNCONFIRMED")
    print(f"Backup created at: {actual_backup_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate false-positive applied records to unconfirmed.")
    parser.add_argument("--database", type=Path, required=True, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying the database")
    parser.add_argument("--apply", action="store_true", help="Apply the migration")
    parser.add_argument("--backup-dir", type=Path, default=Path("output/backups"), help="Directory to store backups")
    parser.add_argument("--report", type=Path, default=Path("output/proposed_submission_status_corrections.json"), help="Path to save the JSON report")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    
    args = parser.parse_args()
    
    # Default is dry run unless apply is specified
    is_dry_run = not args.apply
    if args.dry_run:
        is_dry_run = True
        
    run_migration(
        db_path=args.database,
        dry_run=is_dry_run,
        backup_dir=args.backup_dir,
        report_path=args.report,
        auto_confirm=args.yes
    )
