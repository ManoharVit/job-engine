import pytest
import datetime
from pydantic import ValidationError
from src.models.job import JobRead
from src.models.profile import CandidateProfile, Evidence
from src.eligibility import (
    evaluate_application_eligibility,
    assess_submission_evidence,
    SubmissionEvidence,
    EligibilityJob,
    to_eligibility_job,
    normalize_skill
)

def get_base_job():
    return EligibilityJob(
        title="Software Engineer",
        description="Write code.",
        required_skills=["Python", "SQL"],
        experience_level="Junior",
        application_status="APPROVED"
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
    result = evaluate_application_eligibility(job, None, [], True)
    assert result["decision"] == "SKIP_MISSING_PROFILE"
    assert result["application_action"] == "not_performed"

def test_missing_job_details():
    job = get_base_job().model_copy(update={"description": "   "})
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "SKIP_MISSING_JOB_DETAILS"

    job = get_base_job().model_copy(update={"title": ""})
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "SKIP_MISSING_JOB_DETAILS"

def test_unverified_job():
    job = get_base_job()
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], False)
    assert result["decision"] == "SKIP_UNVERIFIED_JOB"

def test_not_human_approved():
    job = get_base_job().model_copy(update={"application_status": "NEW"})
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "SKIP_NOT_HUMAN_APPROVED"

def test_seniority_mismatch():
    # FAIL case (job level > profile level)
    job = get_base_job().model_copy(update={"experience_level": "Principal"})
    profile = get_base_profile()
    profile.experience_range = "Junior"
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "SKIP_SENIORITY_MISMATCH"

def test_unknown_seniority():
    job = get_base_job().model_copy(update={"experience_level": "Some weird string"})
    profile = get_base_profile()
    profile.experience_range = "Junior"
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["reason"] == "unknown_seniority"
    
def test_overqualified_seniority():
    # REVIEW_REQUIRED case (level_diff >= 2)
    job = get_base_job().model_copy(update={"experience_level": "Junior"})
    profile = get_base_profile()
    profile.experience_range = "Principal"
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["reason"] == "unknown_seniority"

def test_missing_required_skills():
    job = get_base_job()
    profile = get_base_profile()
    evidence = [Evidence(skill="Python", source_type="repo", reference="github.com", verified=True)]
    result = evaluate_application_eligibility(job, profile, evidence, True)
    assert result["decision"] == "SKIP_MISSING_REQUIRED_SKILLS"
    assert "SQL" in result["missing_skills"]

def test_case_insensitive_and_aliases():
    job = get_base_job().model_copy(update={"required_skills": ["Postgres", "python"]})
    profile = get_base_profile()
    profile.must_have_skills = ["PostgreSQL", "PYTHON"]
    
    evidence = [
        Evidence(skill="postgresQL", source_type="repo", reference="github.com", verified=True),
        Evidence(skill="PyThon", source_type="repo", reference="github.com", verified=True)
    ]
    result = evaluate_application_eligibility(job, profile, evidence, True)
    assert result["decision"] == "ELIGIBLE_FOR_LOCAL_DRAFT"

def test_sql_and_postgresql_not_equated():
    assert normalize_skill("SQL") != normalize_skill("PostgreSQL")
    
    job = get_base_job().model_copy(update={"required_skills": ["PostgreSQL"]})
    profile = get_base_profile()
    profile.must_have_skills = ["SQL"]
    evidence = [
        Evidence(skill="SQL", source_type="repo", reference="github.com", verified=True)
    ]
    result = evaluate_application_eligibility(job, profile, evidence, True)
    assert result["decision"] == "SKIP_MISSING_REQUIRED_SKILLS"

def test_deterministic_missing_skills():
    job = get_base_job().model_copy(update={"required_skills": ["Zebra", "Apple", "Banana"]})
    profile = get_base_profile()
    profile.must_have_skills = []
    
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["missing_skills"] == ["Apple", "Banana", "Zebra"]

def test_empty_required_skills():
    job = get_base_job().model_copy(update={"required_skills": []})
    profile = get_base_profile()
    
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["reason"] == "no_required_skills"

def test_eligible():
    job = get_base_job()
    profile = get_base_profile()
    evidence = get_base_evidence()
    result = evaluate_application_eligibility(job, profile, evidence, True)
    assert result["decision"] == "ELIGIBLE_FOR_LOCAL_DRAFT"
    
def test_to_eligibility_job():
    j = JobRead(
        id=1,
        source="test",
        source_job_id="123",
        canonical_url="http://test.com",
        title="Software Engineer",
        company="Tech Corp",
        description="Write code.",
        discovered_at=datetime.datetime.now(datetime.UTC),
        application_status="NEW",
        required_skills=["Python", "SQL"],
        experience_level="Junior"
    )
    ej = to_eligibility_job(j)
    assert ej.title == "Software Engineer"
    assert ej.application_status == "NEW"

def test_eligibility_job_frozen():
    job = get_base_job()
    with pytest.raises(ValidationError):
        job.title = "Mutable"

def test_required_skills_independent_defaults():
    job1 = EligibilityJob(title="J1", application_status="NEW")
    job2 = EligibilityJob(title="J2", application_status="NEW")
    assert job1.required_skills is not job2.required_skills

def test_assess_submission_evidence():
    # Not attempted
    assert assess_submission_evidence(None) == "NOT_ATTEMPTED"
    
    # Invalid evidence type
    with pytest.raises(ValidationError):
        SubmissionEvidence(
            evidence_type="invalid_type",
            observed_at=datetime.datetime.now(datetime.UTC),
            source_reference_hash="abc",
            verified=True
        )

    # Missing timezone
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        SubmissionEvidence(
            evidence_type="platform_confirmation_text",
            observed_at=datetime.datetime.now(),
            source_reference_hash="abc",
            verified=False
        )

    # Missing timezone on verified_at
    with pytest.raises(ValueError, match="verified_at must be timezone-aware"):
        SubmissionEvidence(
            evidence_type="platform_confirmation_text",
            observed_at=datetime.datetime.now(datetime.UTC),
            source_reference_hash="abc",
            verified=True,
            verified_by="Alice",
            verified_at=datetime.datetime.now(),
            review_audit_id=123
        )
        
    # Verified at < observed at
    with pytest.raises(ValueError, match="cannot precede observed_at"):
        SubmissionEvidence(
            evidence_type="platform_confirmation_text",
            observed_at=datetime.datetime.now(datetime.UTC),
            source_reference_hash="abc",
            verified=True,
            verified_by="Alice",
            verified_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
            review_audit_id=123
        )

    # Non-positive review_audit_id
    with pytest.raises(ValidationError):
        SubmissionEvidence(
            evidence_type="platform_confirmation_text",
            observed_at=datetime.datetime.now(datetime.UTC),
            source_reference_hash="abc",
            verified=True,
            verified_by="Alice",
            verified_at=datetime.datetime.now(datetime.UTC),
            review_audit_id=-5
        )

    # Unverified
    ev = SubmissionEvidence(
        evidence_type="platform_confirmation_text",
        observed_at=datetime.datetime.now(datetime.UTC),
        source_reference_hash="abc",
        verified=False
    )
    assert assess_submission_evidence(ev) == "UNCONFIRMED"
    
    # Missing reviewer metadata
    ev.verified = True
    assert assess_submission_evidence(ev) == "UNCONFIRMED"
    
    # Confirmed
    ev.verified_by = "Alice"
    ev.verified_at = datetime.datetime.now(datetime.UTC)
    ev.review_audit_id = 123
    assert assess_submission_evidence(ev) == "CONFIRMED_BY_HUMAN_REVIEW"

def test_boundary_no_forbidden_imports():
    import ast
    with open("src/eligibility.py") as f:
        tree = ast.parse(f.read())
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                assert name.name not in ["requests", "httpx", "urllib", "socket", "subprocess", "os"]
                assert not name.name.startswith("src.api")
                assert not name.name.startswith("src.tracking")
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in ["requests", "httpx", "urllib", "socket", "subprocess", "os"]
            if node.module:
                assert not node.module.startswith("src.api")
                assert not node.module.startswith("src.tracking")

def test_missing_seniority():
    job = get_base_job().model_copy(update={"experience_level": None})
    profile = get_base_profile()
    result = evaluate_application_eligibility(job, profile, [], True)
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["reason"] == "unknown_seniority"
