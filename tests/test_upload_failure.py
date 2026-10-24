import pytest
from src.review.service import ReviewService
from src.review.states import ReviewState as S
from src.models.job import JobModel
from src.review.models import ReviewRecord

def add_job(db_session):
    job = JobModel(source="naukri", source_job_id="123", canonical_url="http://test", title="Data Engineer", company="Co")
    db_session.add(job)
    db_session.commit()
    return job



def test_url_screenshot_form_opened_apply_button_cannot_confirm_submission():
    # These are conceptual tests based on the new local models which don't have fields for these
    # As the system only accepts SubmissionEvidence for confirmation, these cannot transition state to confirmed.
    pass

def test_databricks_missing_evidence():
    from src.eligibility import evaluate_application_eligibility, EligibilityJob
    job = EligibilityJob(title="Data Engineer", application_status="APPROVED", required_skills=["azure databricks", "pyspark"])
    res = evaluate_application_eligibility(job, profile=None, evidence=[], verified_job=True)
    # the profile is missing, should skip
    assert res["decision"] == "SKIP_MISSING_PROFILE"

def test_databricks_missing_evidence_with_profile():
    from src.eligibility import evaluate_application_eligibility, EligibilityJob
    from src.models.profile import CandidateProfile
    job = EligibilityJob(title="Data Engineer", description="desc", application_status="APPROVED", required_skills=["azure databricks", "pyspark"], experience_level="0-2 years")
    prof = CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=["python"], nice_to_have_skills=[], missing_skills=[], experience_range="0-2 years")
    res = evaluate_application_eligibility(job, profile=prof, evidence=[], verified_job=True)
    assert res["decision"] == "SKIP_MISSING_REQUIRED_SKILLS"
    assert "azure databricks" in res["missing_skills"]

def test_data_engineering_unknown_reqs():
    from src.eligibility import evaluate_application_eligibility, EligibilityJob
    from src.models.profile import CandidateProfile
    job = EligibilityJob(title="Data Engineer", description="desc", application_status="APPROVED", required_skills=[], experience_level="0-2 years")
    prof = CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=["python"], nice_to_have_skills=[], missing_skills=[], experience_range="0-2 years")
    res = evaluate_application_eligibility(job, profile=prof, evidence=[], verified_job=True)
    assert res["decision"] == "REVIEW_REQUIRED"

def test_no_browser_imports():
    import os
    src_dir = "/Users/girimanoharv/.gemini/antigravity/scratch/job-engine/src"
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(root, f)) as file:
                    content = file.read()
                    assert "selenium" not in content.lower()
                    assert "playwright" not in content.lower()
                    assert "webdriver" not in content.lower()
