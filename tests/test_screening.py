from datetime import datetime, timezone
from src.screening import ScreeningQuestion, evaluate_screening_question
from src.models.profile import CandidateProfile, Evidence

def test_in_person_interview_question():
    q = ScreeningQuestion(question_id="1", question_text="Will you be comfortable to attend an in-person interview?", question_type="yes_no")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=[], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [])
    assert res.classification == "REQUIRES_HUMAN_ANSWER"
    assert res.external_action == "not_performed"

def test_shift_timing_question():
    q = ScreeningQuestion(question_id="2", question_text="Are you comfortable with night shift?", question_type="yes_no")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=[], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [])
    assert res.classification == "REQUIRES_HUMAN_ANSWER"

def test_salary_question():
    q = ScreeningQuestion(question_id="3", question_text="What is your expected salary?", question_type="numeric")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=[], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [])
    assert res.classification in ("SENSITIVE_OR_LEGAL", "REQUIRES_HUMAN_ANSWER")

def test_work_authorization_question():
    q = ScreeningQuestion(question_id="4", question_text="Do you have work authorization?", question_type="yes_no")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=[], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [])
    assert res.classification == "SENSITIVE_OR_LEGAL"

def test_verified_python_skills():
    q = ScreeningQuestion(question_id="5", question_text="Do you have Python experience?", question_type="yes_no")
    ev = Evidence(skill="Python", verified=True, source="test", discovered_at=datetime.now(timezone.utc), verification_source="test", source_type="resume", reference="file.pdf")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=["Python"], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [ev])
    assert res.classification == "SAFE_TO_SUGGEST"
    assert "Python" in res.evidence_references

def test_unverified_databricks():
    q = ScreeningQuestion(question_id="6", question_text="Do you have Azure Databricks experience?", question_type="yes_no")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=[], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [])
    assert res.classification == "UNSUPPORTED_CLAIM"

def test_malformed_question():
    q = ScreeningQuestion(question_id="7", question_text="XYZ", question_type="unknown")
    res = evaluate_screening_question(q, CandidateProfile(email="test@test.com", phone="123", current_title="dev", current_company="co", must_have_skills=[], nice_to_have_skills=[], missing_skills=[], experience_range="0-1"), [])
    assert res.classification == "AMBIGUOUS"

def test_resume_upload_events_do_not_create_answers():
    # Placeholder for logic verification
    pass

def test_years_of_experience():
    q = ScreeningQuestion(question_id="8", question_text="How many years of experience do you have in python?", question_type="numeric")
    ev1 = Evidence(skill="Python", verified=True, source_type="resume", reference="file.pdf", structured_duration=2, reviewer="giri")
    res = evaluate_screening_question(q, CandidateProfile(), [ev1])
    assert res.classification == "SAFE_TO_SUGGEST"
    assert res.suggested_answer == "2"

def test_years_of_experience_no_duration():
    q = ScreeningQuestion(question_id="9", question_text="How many years of experience do you have in python?", question_type="numeric")
    ev1 = Evidence(skill="Python", verified=True, source_type="resume", reference="file.pdf")
    res = evaluate_screening_question(q, CandidateProfile(), [ev1])
    assert res.classification == "UNSUPPORTED_CLAIM"
    assert res.suggested_answer is None
    assert "Answer manually only if you know the accurate value" in res.reason
