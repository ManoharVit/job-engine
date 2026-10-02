import pytest
from pydantic import ValidationError
from src.models.job import JobCreate
from src.models.profile import CandidateProfile, Evidence

def test_job_create_valid():
    job_data = {
        "source": "manual",
        "source_job_id": "job123",
        "canonical_url": "https://example.com/job",
        "title": "Data Engineer",
        "company": "Tech Corp"
    }
    job = JobCreate(**job_data)
    assert job.title == "Data Engineer"
    assert str(job.canonical_url) == "https://example.com/job"
    assert job.remote_status is None
    assert job.required_skills == []

def test_job_create_invalid_url():
    job_data = {
        "source": "manual",
        "source_job_id": "job123",
        "canonical_url": "not-a-url",
        "title": "Data Engineer",
        "company": "Tech Corp"
    }
    with pytest.raises(ValidationError):
        JobCreate(**job_data)

def test_candidate_profile_valid():
    evidence = Evidence(skill="Python", source_type="resume", reference="line 4")
    profile = CandidateProfile(
        target_roles=["Data Engineer"],
        must_have_skills=["Python", "SQL"],
        evidence_registry=[evidence]
    )
    assert "Python" in profile.must_have_skills
    assert profile.evidence_registry[0].skill == "Python"
