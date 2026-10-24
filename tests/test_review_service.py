"""Persistence-layer tests for the local review workflow (in-memory SQLite only)."""
import json
from datetime import timedelta, UTC, datetime

import pytest

from src.models.audit import AuditLog
from src.models.job import Draft, JobModel
from src.review.evidence import SubmissionEvidenceRecord
from src.review.logs import job_ref_hash
from src.review.models import ReviewRecord, SubmissionEvidenceRow
from src.review.service import ReviewService
from src.review.states import ReviewState, ReviewTransitionError
from tests.review_fixtures import LONG_DESCRIPTION, make_evidence, make_profile

S = ReviewState
URL = "https://www.naukri.com/job-listings-python-backend-engineer-example-123"


def add_job(db, **over):
    data = dict(
        source="naukri_reference", source_job_id=over.pop("sid", "nk-1"), canonical_url=URL,
        title="Python Backend Engineer (FastAPI)", company="Example Payments Pvt Ltd",
        location="Hyderabad", employment_type="Full-time", description=LONG_DESCRIPTION,
        required_skills=["Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "pytest"],
        experience_level="1-3 Yrs",
    )
    data.update(over)
    job = JobModel(**data)
    db.add(job)
    db.commit()
    return job


def audit_actions(db, job_id):
    return [a.action for a in db.query(AuditLog).filter_by(entity="ReviewRecord", entity_id=str(job_id)).order_by(AuditLog.id)]


def to_eligible(db, svc, job):
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    approval = svc.record_review_approval(job.id, verified_by="giri")
    log = svc.evaluate(job.id, make_profile(), make_evidence(), approval)
    assert log.to_state == S.ELIGIBLE_FOR_LOCAL_DRAFT
    return approval


def to_drafted(db, svc, job):
    to_eligible(db, svc, job)
    db.add(Draft(job_id=job.id, document_type="cover_letter", version=1, content="local draft"))
    db.commit()
    svc.mark_drafted(job.id, verified_by="giri")


# ---------- Automatic path stops at ELIGIBLE ----------

def test_automatic_path_stops_at_eligible(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_eligible(db_session, svc, job)
    assert svc.state(job.id) == S.ELIGIBLE_FOR_LOCAL_DRAFT
    for log in svc.history(job.id):
        assert log.external_action == "none"
        assert log.submission_claim == "none"


def test_evaluate_without_approval_requires_review(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    log = svc.evaluate(job.id, make_profile(), make_evidence(), None)
    assert log.to_state == S.REVIEW_REQUIRED
    assert "awaiting_review_approval" in log.reasons


def test_reevaluation_after_approval_moves_review_to_eligible(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    svc.evaluate(job.id, make_profile(), make_evidence(), None)
    approval = svc.record_review_approval(job.id, verified_by="giri")
    log = svc.evaluate(job.id, make_profile(), make_evidence(), approval)
    assert log.to_state == S.ELIGIBLE_FOR_LOCAL_DRAFT


def test_approval_for_other_job_is_rejected(db_session):
    svc = ReviewService(db_session)
    j1, j2 = add_job(db_session), add_job(db_session, sid="nk-2", canonical_url=URL + "-b")
    for j in (j1, j2):
        svc.register(j.id, source="naukri_reference", source_verified=True)
        svc.normalize(j.id)
    approval_for_j2 = svc.record_review_approval(j2.id, verified_by="giri")
    with pytest.raises(ReviewTransitionError, match="approval"):
        svc.evaluate(j1.id, make_profile(), make_evidence(), approval_for_j2)


def test_senior_job_is_skipped_and_terminal(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session, title="Senior Python Developer")
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    approval = svc.record_review_approval(job.id, verified_by="giri")
    log = svc.evaluate(job.id, make_profile(), make_evidence(), approval)
    assert log.to_state == S.SKIPPED
    with pytest.raises(ReviewTransitionError):
        svc.mark_drafted(job.id, verified_by="giri")


def test_normalize_skips_record_without_title(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session, title="  ")
    svc.register(job.id, source="naukri_reference", source_verified=True)
    log = svc.normalize(job.id)
    assert log.to_state == S.SKIPPED and "missing_title" in log.reasons


def test_duplicate_is_terminal(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    svc.mark_duplicate(job.id, duplicate_of_job_id=999)
    assert svc.state(job.id) == S.DUPLICATE
    with pytest.raises(ReviewTransitionError):
        svc.evaluate(job.id, make_profile(), make_evidence(), None)


# ---------- Human decisions ----------

def test_human_override_from_review_requires_reason(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session, experience_level=None)
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    approval = svc.record_review_approval(job.id, verified_by="giri")
    assert svc.evaluate(job.id, make_profile(), make_evidence(), approval).to_state == S.REVIEW_REQUIRED
    with pytest.raises(ReviewTransitionError):
        svc.human_decide(job.id, S.ELIGIBLE_FOR_LOCAL_DRAFT, verified_by="giri", reason="  ")
    log = svc.human_decide(job.id, S.ELIGIBLE_FOR_LOCAL_DRAFT, verified_by="giri", reason="JD says 1-2 yrs in body")
    assert log.actor == "human" and log.to_state == S.ELIGIBLE_FOR_LOCAL_DRAFT


def test_human_decide_cannot_target_submission_states(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session, experience_level=None)
    svc.register(job.id, source="naukri_reference", source_verified=True)
    svc.normalize(job.id)
    svc.evaluate(job.id, make_profile(), make_evidence(), None)
    for target in (S.APPROVED_FOR_EXPORT, S.SUBMISSION_UNCONFIRMED, S.CONFIRMED_BY_HUMAN_REVIEW):
        with pytest.raises(ReviewTransitionError):
            svc.human_decide(job.id, target, verified_by="giri", reason="x")


def test_mark_drafted_requires_draft_row(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_eligible(db_session, svc, job)
    with pytest.raises(ReviewTransitionError, match="Draft"):
        svc.mark_drafted(job.id, verified_by="giri")


# ---------- Export gate ----------

def test_export_requires_reviewer(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_drafted(db_session, svc, job)
    with pytest.raises(ReviewTransitionError):
        svc.approve_export(job.id, verified_by=" ", profile=make_profile(), evidence=make_evidence())
    assert svc.state(job.id) == S.DRAFTED


def test_export_requires_verified_evidence_for_every_required_skill(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_drafted(db_session, svc, job)
    weaker = make_evidence(skills=("Python", "FastAPI", "PostgreSQL", "SQLAlchemy"), unverified=("pytest",))
    with pytest.raises(ReviewTransitionError, match="verified evidence"):
        svc.approve_export(job.id, verified_by="giri", profile=make_profile(), evidence=weaker)
    assert svc.state(job.id) == S.DRAFTED


def test_export_rejected_when_rubric_now_skips(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_drafted(db_session, svc, job)
    job.title = "Lead Python Engineer"
    db_session.commit()
    with pytest.raises(ReviewTransitionError, match="hard rule"):
        svc.approve_export(job.id, verified_by="giri", profile=make_profile(), evidence=make_evidence())


def test_export_success_then_not_attempted(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_drafted(db_session, svc, job)
    log = svc.approve_export(job.id, verified_by="giri", profile=make_profile(), evidence=make_evidence())
    assert log.to_state == S.APPROVED_FOR_EXPORT and log.actor == "human"
    package, log2 = svc.mark_exported(job.id)
    assert log2.to_state == S.EXTERNAL_APPLICATION_NOT_ATTEMPTED
    assert package["external_action"] == "none"
    assert package["submission_status"] == "EXTERNAL_APPLICATION_NOT_ATTEMPTED"
    assert package["draft"] == "local draft"


def test_export_gate_is_atomic_on_commit_failure(db_session, monkeypatch):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_drafted(db_session, svc, job)
    n_audit = db_session.query(AuditLog).count()

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db_session, "commit", boom)
    with pytest.raises(RuntimeError):
        svc.approve_export(job.id, verified_by="giri", profile=make_profile(), evidence=make_evidence())
    monkeypatch.undo()
    assert db_session.query(AuditLog).count() == n_audit
    assert svc.state(job.id) == S.DRAFTED


def test_cannot_export_before_drafted(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_eligible(db_session, svc, job)
    with pytest.raises(ReviewTransitionError):
        svc.approve_export(job.id, verified_by="giri", profile=make_profile(), evidence=make_evidence())
    with pytest.raises(ReviewTransitionError):
        svc.mark_exported(job.id)


# ---------- Submission evidence ----------

def to_not_attempted(db, svc, job):
    to_drafted(db, svc, job)
    svc.approve_export(job.id, verified_by="giri", profile=make_profile(), evidence=make_evidence())
    svc.mark_exported(job.id)


def make_record(job_hash, audit_id, **over):
    now = datetime.now(UTC)
    data = dict(
        evidence_id="7f1c2d3e-4b5a-4c6d-8e9f-0a1b2c3d4e5f", job_ref_hash=job_hash,
        evidence_type="platform_status_record", observed_at=now - timedelta(minutes=10),
        source_reference_hash=job_ref_hash("naukri-applied-jobs-entry"), verified_by="giri",
        verified_at=now - timedelta(minutes=5), review_audit_id=audit_id,
    )
    data.update(over)
    return SubmissionEvidenceRecord(**data)


def test_confirmation_without_evidence_is_impossible(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    log = svc.report_unconfirmed_submission(job.id, verified_by="giri", note="Applied on employer site manually")
    assert log.to_state == S.SUBMISSION_UNCONFIRMED and log.submission_claim == "unconfirmed"
    with pytest.raises(TypeError):
        svc.confirm_submission(job.id)  # evidence argument is mandatory
    with pytest.raises(ReviewTransitionError):
        svc.confirm_submission(job.id, None)
    assert svc.state(job.id) == S.SUBMISSION_UNCONFIRMED


def test_confirmation_rejects_mismatched_job_hash(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    audit_id = svc.open_evidence_review(job.id, verified_by="giri")
    rec = make_record(job_ref_hash("https://www.naukri.com/some-other-job"), audit_id)
    with pytest.raises(ReviewTransitionError, match="job_ref_hash"):
        svc.confirm_submission(job.id, rec)


def test_confirmation_rejects_unknown_or_foreign_audit_reference(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    other = add_job(db_session, sid="nk-2", canonical_url=URL + "-b")
    to_not_attempted(db_session, svc, job)
    svc.register(other.id, source="naukri_reference", source_verified=True)
    foreign_audit = svc.open_evidence_review(other.id, verified_by="giri")
    h = job_ref_hash(URL)
    with pytest.raises(ReviewTransitionError, match="audit"):
        svc.confirm_submission(job.id, make_record(h, "999999"))
    with pytest.raises(ReviewTransitionError, match="audit"):
        svc.confirm_submission(job.id, make_record(h, foreign_audit))


def test_confirmation_rejects_reviewer_mismatch(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    audit_id = svc.open_evidence_review(job.id, verified_by="giri")
    with pytest.raises(ReviewTransitionError, match="reviewer"):
        svc.confirm_submission(job.id, make_record(job_ref_hash(URL), audit_id, verified_by="someone-else"))


def test_valid_human_evidence_confirms_and_persists_atomically(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    svc.report_unconfirmed_submission(job.id, verified_by="giri", note="Applied via Naukri")
    audit_id = svc.open_evidence_review(job.id, verified_by="giri")
    log = svc.confirm_submission(job.id, make_record(job_ref_hash(URL), audit_id))
    assert log.to_state == S.CONFIRMED_BY_HUMAN_REVIEW
    assert log.actor == "human" and log.evidence_record_id is not None
    assert log.submission_claim == "confirmed_by_human_review"
    assert db_session.query(SubmissionEvidenceRow).filter_by(job_id=job.id).count() == 1
    assert "SUBMISSION_CONFIRMED_BY_HUMAN" in audit_actions(db_session, job.id)


def test_confirmation_directly_from_not_attempted_is_allowed_with_evidence(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    audit_id = svc.open_evidence_review(job.id, verified_by="giri")
    log = svc.confirm_submission(job.id, make_record(job_ref_hash(URL), audit_id, evidence_type="confirmation_email"))
    assert log.to_state == S.CONFIRMED_BY_HUMAN_REVIEW


def test_confirmation_is_atomic_on_commit_failure(db_session, monkeypatch):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    audit_id = svc.open_evidence_review(job.id, verified_by="giri")
    rec = make_record(job_ref_hash(URL), audit_id)

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db_session, "commit", boom)
    with pytest.raises(RuntimeError):
        svc.confirm_submission(job.id, rec)
    monkeypatch.undo()
    assert db_session.query(SubmissionEvidenceRow).count() == 0
    assert svc.state(job.id) == S.EXTERNAL_APPLICATION_NOT_ATTEMPTED


def test_audit_details_are_sanitized(db_session):
    svc = ReviewService(db_session)
    job = add_job(db_session)
    to_not_attempted(db_session, svc, job)
    for row in db_session.query(AuditLog).filter_by(entity="ReviewRecord").all():
        assert URL not in (row.details or "")
        assert "applied successfully" not in (row.details or "").lower()
        json.loads(row.details)


def test_review_tables_created_by_metadata(db_session):
    from sqlalchemy import inspect
    names = inspect(db_session.get_bind()).get_table_names()
    assert "review_records" in names and "submission_evidence" in names
    assert ReviewRecord.__tablename__ == "review_records"
