import pytest
import datetime
from src.models.job import JobRead
from src.models.profile import CandidateProfile, Evidence
from src.eligibility import (
    evaluate_application_eligibility,
    assess_submission_evidence,
    SubmissionEvidence
)

def get_base_job():
    return JobRead(
        id=1,
        source="test",
        source_job_id="123",
        canonical_url="http://test.com",
        title="Software Engineer",
        company="Tech Corp",
        description="Write code.",
        discovered_at=datetime.datetime.now(datetime.UTC),
        application_status="NEW",
        required_skills=["Python", "SQL"]
    )

def get_base_profile():
    return CandidateProfile(
        experience_range="Junior",
        must_have_skills=["Python", "SQL"],
        nice_to_have_skills=[],
        excluded_skills=[],
        target_roles=[],
        evidence_registry=[]
    )

def get_base_evidence():
    return [
        Evidence(skill="Python", source_type="repo", reference="github.com", verified=True),
        Evidence(skill="SQL", source_type="repo", reference="github.com", verified=True)
    ]

def test_missing_profile():
    job = get_base_job()
    result = evaluate_application_eligibility(job, None, [], True, True)
    assert result["decision"] == "SKIP_MISSING_PROFILE"
    assert result["application_action"] == "not_performed"

def test_missing_job_details():
    job = get_base_job()
    job.description = None
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], True, True)
    assert result["decision"] == "SKIP_MISSING_JOB_DETAILS"

def test_unverified_job():
    job = get_base_job()
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], True, False)
    assert result["decision"] == "SKIP_UNVERIFIED_JOB"

def test_not_human_approved():
    job = get_base_job()
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], False, True)
    assert result["decision"] == "SKIP_NOT_HUMAN_APPROVED"

def test_seniority_mismatch():
    job = get_base_job()
    job.experience_level = "Principal"
    profile = get_base_profile()
    profile.experience_range = "Junior"
    result = evaluate_application_eligibility(job, profile, [], True, True)
    assert result["decision"] == "SKIP_SENIORITY_MISMATCH"

def test_missing_required_skills():
    job = get_base_job()
    profile = get_base_profile()
    evidence = [Evidence(skill="Python", source_type="repo", reference="github.com", verified=True)]
    # SQL evidence is missing
    result = evaluate_application_eligibility(job, profile, evidence, True, True)
    assert result["decision"] == "SKIP_MISSING_REQUIRED_SKILLS"
    assert "SQL" in result["missing_skills"]

def test_eligible():
    job = get_base_job()
    profile = get_base_profile()
    evidence = get_base_evidence()
    result = evaluate_application_eligibility(job, profile, evidence, True, True)
    assert result["decision"] == "ELIGIBLE_FOR_LOCAL_DRAFT"

def test_assess_submission_evidence():
    # Not attempted
    assert assess_submission_evidence(None) == "NOT_ATTEMPTED"
    
    # Unconfirmed
    ev = SubmissionEvidence(
        evidence_type="platform_confirmation_text",
        observed_at=datetime.datetime.now(datetime.UTC),
        source_reference_hash="abc",
        verified=False
    )
    assert assess_submission_evidence(ev) == "UNCONFIRMED"
    
    # Confirmed
    ev.verified = True
    assert assess_submission_evidence(ev) == "CONFIRMED_BY_HUMAN_REVIEW"


def test_missing_profile_skill():
    job = get_base_job()
    job.required_skills.append("C++")
    profile = get_base_profile()
    evidence = get_base_evidence()
    result = evaluate_application_eligibility(job, profile, evidence, True, True)
    assert result["decision"] == "SKIP_MISSING_REQUIRED_SKILLS"
    assert "C++" in result["missing_skills"]
