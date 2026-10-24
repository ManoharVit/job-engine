"""State machine, evidence schema, and log schema tests."""
import json
from datetime import datetime, timedelta, UTC

import pytest
from pydantic import ValidationError

from src.review.evidence import SubmissionEvidenceRecord
from src.review.logs import LocalLogRecord, export_json_schemas, job_ref_hash
from src.review.states import (
    SYSTEM_ALLOWED_TARGETS, Actor, ReviewState, ReviewTransitionError, check_transition,
)

S = ReviewState
HASH = job_ref_hash("https://www.naukri.com/job-listings-x-123")
OTHER_HASH = job_ref_hash("https://www.naukri.com/job-listings-y-456")
PAST = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


# ---------- State machine ----------

def test_happy_path_requires_human_after_eligible():
    check_transition(S.DISCOVERED, S.NORMALIZED, Actor.SYSTEM)
    check_transition(S.NORMALIZED, S.ELIGIBLE_FOR_LOCAL_DRAFT, Actor.SYSTEM)
    check_transition(S.ELIGIBLE_FOR_LOCAL_DRAFT, S.DRAFTED, Actor.HUMAN)
    check_transition(S.DRAFTED, S.APPROVED_FOR_EXPORT, Actor.HUMAN)
    check_transition(S.APPROVED_FOR_EXPORT, S.EXTERNAL_APPLICATION_NOT_ATTEMPTED, Actor.SYSTEM)
    check_transition(S.EXTERNAL_APPLICATION_NOT_ATTEMPTED, S.SUBMISSION_UNCONFIRMED, Actor.HUMAN)
    check_transition(S.SUBMISSION_UNCONFIRMED, S.CONFIRMED_BY_HUMAN_REVIEW, Actor.HUMAN)


ALL_PAIRS = [(a, b) for a in S for b in S]


@pytest.mark.parametrize("src,dst", ALL_PAIRS)
def test_system_actor_never_reaches_beyond_allowed_targets(src, dst):
    if dst in SYSTEM_ALLOWED_TARGETS:
        pytest.skip("allowed target set")
    with pytest.raises(ReviewTransitionError):
        check_transition(src, dst, Actor.SYSTEM)


def test_system_allowed_targets_are_exactly_the_safe_set():
    assert SYSTEM_ALLOWED_TARGETS == {
        S.NORMALIZED, S.DUPLICATE, S.SKIPPED, S.REVIEW_REQUIRED,
        S.ELIGIBLE_FOR_LOCAL_DRAFT, S.EXTERNAL_APPLICATION_NOT_ATTEMPTED,
    }


@pytest.mark.parametrize("src", [S.DISCOVERED, S.NORMALIZED, S.REVIEW_REQUIRED, S.ELIGIBLE_FOR_LOCAL_DRAFT, S.DRAFTED])
def test_no_shortcut_to_confirmed(src):
    for actor in Actor:
        with pytest.raises(ReviewTransitionError):
            check_transition(src, S.CONFIRMED_BY_HUMAN_REVIEW, actor)


@pytest.mark.parametrize("terminal", [S.SKIPPED, S.DUPLICATE, S.CONFIRMED_BY_HUMAN_REVIEW])
def test_terminal_states(terminal):
    for dst in S:
        for actor in Actor:
            with pytest.raises(ReviewTransitionError):
                check_transition(terminal, dst, actor)


# ---------- Evidence record ----------

def evidence_kwargs(**over):
    data = dict(
        evidence_id="7f1c2d3e-4b5a-4c6d-8e9f-0a1b2c3d4e5f",
        job_ref_hash=HASH,
        evidence_type="platform_status_record",
        observed_at=PAST,
        source_reference_hash=job_ref_hash("naukri-applied-list-entry-123"),
        verified_by="giri",
        verified_at=PAST + timedelta(minutes=5),
        review_audit_id="7",
    )
    data.update(over)
    return data


def test_valid_evidence_record():
    rec = SubmissionEvidenceRecord(**evidence_kwargs())
    assert rec.evidence_type == "platform_status_record"


@pytest.mark.parametrize("extra", ["screenshot_path", "current_url", "page_text", "apply_clicked"])
def test_evidence_rejects_screenshot_url_and_click_fields(extra):
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(**{extra: "anything"}))


@pytest.mark.parametrize("ref", ["https://www.naukri.com/myapply", "logs/screenshots/nk_x.png", "sha256:abc"])
def test_evidence_source_reference_must_be_a_hash(ref):
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(source_reference_hash=ref))


@pytest.mark.parametrize("etype", ["screenshot", "url_change", "apply_clicked", "navigation_success"])
def test_evidence_type_whitelist(etype):
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(evidence_type=etype))


def test_evidence_reviewed_before_observed_rejected():
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(verified_at=PAST - timedelta(minutes=1)))


def test_evidence_future_timestamp_rejected():
    future = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(observed_at=future, verified_at=future))


def test_evidence_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(observed_at=datetime(2026, 10, 1, 9, 0)))


@pytest.mark.parametrize("field", ["verified_by", "review_audit_id"])
def test_evidence_requires_reviewer_and_audit(field):
    with pytest.raises(ValidationError):
        SubmissionEvidenceRecord(**evidence_kwargs(**{field: "  "}))


# ---------- Log record ----------

def log_kwargs(**over):
    data = dict(
        actor="system", source="naukri_reference", job_ref_hash=HASH,
        title="Python Developer", company="Example", profile_version="v1",
        from_state="NORMALIZED", to_state="SKIPPED", decision="SKIP",
        reasons=["experience_min_years_ge_5"], submission_claim="none",
        message="Skipped: experience outside target range.",
    )
    data.update(over)
    return data


def test_valid_log_record_has_constant_external_action():
    rec = LocalLogRecord(**log_kwargs())
    assert rec.external_action == "none"
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(external_action="clicked_apply"))


@pytest.mark.parametrize("msg", ["✅ Applied successfully!", "APPLIED SUCCESSFULLY", "applied   successfully (after questionnaire)"])
def test_log_rejects_applied_successfully_wording(msg):
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(message=msg))


@pytest.mark.parametrize("extra", ["screenshot_path", "current_url", "raw_url", "page_text", "cookies"])
def test_log_rejects_unsanitized_fields(extra):
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(**{extra: "x"}))


def test_log_job_ref_must_be_hash_not_url():
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(job_ref_hash="https://www.naukri.com/job-listings-x-123"))


def test_confirmed_claim_requires_human_evidence_and_confirmed_state():
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(submission_claim="confirmed_by_human_review", to_state="CONFIRMED_BY_HUMAN_REVIEW"))
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(actor="human", verified_by="giri", evidence_record_id="e1",
                                    submission_claim="confirmed_by_human_review", to_state="SUBMISSION_UNCONFIRMED",
                                    from_state="EXTERNAL_APPLICATION_NOT_ATTEMPTED", decision="STATE_CHANGE"))
    rec = LocalLogRecord(**log_kwargs(actor="human", verified_by="giri", evidence_record_id="e1",
                                      submission_claim="confirmed_by_human_review",
                                      from_state="SUBMISSION_UNCONFIRMED", to_state="CONFIRMED_BY_HUMAN_REVIEW",
                                      decision="STATE_CHANGE", reasons=["platform_status_record"],
                                      message="Confirmed by human review."))
    assert rec.submission_claim == "confirmed_by_human_review"


def test_confirmed_state_requires_confirmed_claim():
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(actor="human", verified_by="giri", evidence_record_id="e1",
                                    from_state="SUBMISSION_UNCONFIRMED", to_state="CONFIRMED_BY_HUMAN_REVIEW",
                                    decision="STATE_CHANGE", submission_claim="unconfirmed"))


def test_system_log_cannot_claim_submission_states():
    with pytest.raises(ValidationError):
        LocalLogRecord(**log_kwargs(from_state="EXTERNAL_APPLICATION_NOT_ATTEMPTED",
                                    to_state="SUBMISSION_UNCONFIRMED", decision="STATE_CHANGE",
                                    submission_claim="unconfirmed"))


def test_job_ref_hash_is_canonical_and_stable():
    a = job_ref_hash("https://WWW.naukri.com/job-listings-x-123/?utm_source=mail")
    b = job_ref_hash("https://www.naukri.com/job-listings-x-123")
    assert a == b and a.startswith("sha256:") and len(a) == 7 + 64


def test_exported_json_schemas_forbid_additional_properties():
    schemas = export_json_schemas()
    assert set(schemas) == {"local_log_record", "submission_evidence"}
    for s in schemas.values():
        assert s["additionalProperties"] is False
    json.dumps(schemas)  # serializable
