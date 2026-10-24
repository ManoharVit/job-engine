"""Eligibility rubric tests for the candidate's target roles. Pure functions only."""
import pytest

from src.review.rubric import evaluate_job, parse_experience
from src.review.states import ReviewState
from tests.review_fixtures import make_approval, make_evidence, make_job, make_profile


def run(job=None, profile="default", evidence=None, approval="default"):
    return evaluate_job(
        job or make_job(),
        make_profile() if profile == "default" else profile,
        make_evidence() if evidence is None else evidence,
        make_approval() if approval == "default" else approval,
    )


# ---------- Eligible baseline ----------

def test_python_fastapi_role_is_eligible_for_local_draft():
    d = run()
    assert d.decision == "ELIGIBLE_FOR_LOCAL_DRAFT"
    assert d.target_state == ReviewState.ELIGIBLE_FOR_LOCAL_DRAFT
    assert d.external_action == "none"
    assert d.missing_skills == () and d.unverified_skills == ()
    assert "priority:fintech_domain" in d.priority
    assert "priority:fastapi_postgresql" in d.priority


def test_decision_is_deterministic():
    assert run().model_dump_json() == run().model_dump_json()


# ---------- Missing profile / evidence / details ----------

def test_missing_profile_skips():
    d = run(profile=None)
    assert d.decision == "SKIP" and "missing_candidate_profile" in d.reasons


def test_missing_all_candidate_evidence_skips():
    d = run(evidence=[])
    assert d.decision == "SKIP" and "missing_candidate_evidence" in d.reasons


def test_only_unverified_evidence_skips():
    d = run(evidence=make_evidence(skills=(), unverified=("Python", "FastAPI")))
    assert d.decision == "SKIP" and "missing_candidate_evidence" in d.reasons


@pytest.mark.parametrize("field", ["title", "company"])
def test_missing_title_or_company_skips(field):
    d = run(job=make_job(**{field: "   "}))
    assert d.decision == "SKIP" and f"missing_{field}" in d.reasons


@pytest.mark.parametrize("desc", [None, "", "   ", "Python role."])
def test_missing_or_short_description_requires_review(desc):
    d = run(job=make_job(description=desc))
    assert d.decision == "REVIEW_REQUIRED" and "incomplete_job_details" in d.reasons


def test_empty_required_skills_requires_review():
    d = run(job=make_job(required_skills=[]))
    assert d.decision == "REVIEW_REQUIRED" and "no_required_skills" in d.reasons


def test_unknown_seniority_requires_review():
    d = run(job=make_job(experience_text=None))
    assert d.decision == "REVIEW_REQUIRED" and "unknown_seniority" in d.reasons


def test_unverified_job_record_requires_review():
    d = run(job=make_job(source_verified=False))
    assert d.decision == "REVIEW_REQUIRED" and "unverified_job_record" in d.reasons


def test_missing_review_approval_requires_review():
    d = run(approval=None)
    assert d.decision == "REVIEW_REQUIRED" and "awaiting_review_approval" in d.reasons


# ---------- Seniority / experience ----------

@pytest.mark.parametrize("text", ["5+ Yrs", "5-8 Yrs", "6 years", "minimum 7 years", "at least 5 years", "8 to 12 years"])
def test_five_plus_years_skipped(text):
    d = run(job=make_job(experience_text=text))
    assert d.decision == "SKIP" and "experience_min_years_ge_5" in d.reasons


def test_five_plus_years_in_description_skipped():
    desc = make_job().description + " Requires at least 6 years of backend experience."
    d = run(job=make_job(description=desc))
    assert d.decision == "SKIP" and "experience_min_years_ge_5" in d.reasons


@pytest.mark.parametrize("title", [
    "Senior Python Developer", "Sr. Backend Engineer", "Sr Python Engineer", "Lead Python Engineer",
    "Principal Backend Engineer", "Staff Software Engineer", "Solutions Architect - Python",
    "Engineering Manager", "Director of Engineering", "Head of Backend", "Tech Lead - FastAPI",
])
def test_seniority_titles_skipped(title):
    d = run(job=make_job(title=title))
    assert d.decision == "SKIP"
    assert any(r.startswith("title_seniority_excluded:") for r in d.reasons)


@pytest.mark.parametrize("title,company", [
    ("Python Developer - Leadership Analytics Platform", "Example"),
    ("Python Backend Engineer", "Staffnix"),
    ("Python Backend Engineer", "LeadSquared"),
])
def test_seniority_rules_use_whole_words(title, company):
    d = run(job=make_job(title=title, company=company))
    assert not any(r.startswith("title_seniority_excluded:") for r in d.reasons)


def test_stretch_three_to_four_years_with_strong_match_requires_review():
    d = run(job=make_job(experience_text="3-4 Yrs"))
    assert d.decision == "REVIEW_REQUIRED" and "stretch_seniority_strong_match" in d.reasons


def test_stretch_with_weak_match_skips():
    job = make_job(experience_text="3-5 Yrs", required_skills=["Python", "FastAPI", "Docker", "Django", "LLM"])
    d = run(job=job)  # Docker/Django/LLM in profile but unverified -> 2/5 verified
    assert d.decision == "SKIP" and "stretch_seniority_weak_match" in d.reasons


def test_wide_experience_range_requires_review():
    d = run(job=make_job(experience_text="2-7 Yrs"))
    assert d.decision == "REVIEW_REQUIRED" and "ambiguous_experience_range" in d.reasons


@pytest.mark.parametrize("text,expected", [
    ("1-3 Yrs", (1, 3)), ("0 to 2 years", (0, 2)), ("5+ yrs", (5, None)),
    ("minimum 7 years", (7, None)), ("Entry level", (None, None)),
])
def test_parse_experience(text, expected):
    e = parse_experience(text, "", "Python Developer")
    assert (e.min_years, e.max_years) == expected


# ---------- Skills ----------

def test_required_skill_not_in_profile_skips():
    d = run(job=make_job(required_skills=["Python", "ROS2", "SLAM"]))
    assert d.decision == "SKIP"
    assert "missing_required_skills" in d.reasons
    assert d.missing_skills == ("ROS2", "SLAM")


@pytest.mark.parametrize("skill", ["Spark", "Airflow", "dbt", "Kafka"])
def test_unverified_data_platform_skill_skips(skill):
    d = run(job=make_job(required_skills=["Python", skill]))
    assert d.decision == "SKIP" and skill in d.missing_skills


def test_required_skill_in_profile_but_unverified_requires_review():
    d = run(job=make_job(required_skills=["Python", "Django"]),
            evidence=make_evidence(unverified=("Django",)))
    assert d.decision == "REVIEW_REQUIRED"
    assert "unverified_required_skill" in d.reasons
    assert d.unverified_skills == ("Django",)


def test_excluded_skill_conflict_skips():
    d = run(job=make_job(required_skills=["Python", "Java"]))
    assert d.decision == "SKIP" and "excluded_skill_conflict" in d.reasons


def test_skill_alias_normalization():
    d = run(job=make_job(required_skills=["python", "FASTAPI", "Postgres", "sqlalchemy", "PyTest"]))
    assert d.decision == "ELIGIBLE_FOR_LOCAL_DRAFT"


def test_unfamiliar_infra_in_description_requires_review():
    desc = make_job().description + " You will also own Kubernetes and Terraform deployments."
    d = run(job=make_job(description=desc))
    assert d.decision == "REVIEW_REQUIRED" and "mixed_infra_requirements" in d.reasons


# ---------- Stack / role family ----------

@pytest.mark.parametrize("title", ["Java Backend Developer", ".NET Developer", "PHP Developer", "C# Engineer"])
def test_java_dotnet_php_primary_skipped(title):
    d = run(job=make_job(title=title))
    assert d.decision == "SKIP" and any(r.startswith("primary_stack_excluded:") for r in d.reasons)


def test_javascript_is_not_java():
    d = run(job=make_job(title="Python Developer (JavaScript a plus)"))
    assert not any(r.startswith("primary_stack_excluded:") for r in d.reasons)


@pytest.mark.parametrize("title", ["Data Analyst", "BI Developer", "Power BI Developer", "Tableau Developer"])
def test_weak_fit_titles_skipped(title):
    d = run(job=make_job(title=title))
    assert d.decision == "SKIP" and "weak_fit_title" in d.reasons


def test_research_scientist_skipped():
    d = run(job=make_job(title="Research Scientist, LLMs"))
    assert d.decision == "SKIP" and "research_role" in d.reasons


def test_phd_required_in_description_skipped():
    desc = make_job().description + " A PhD in machine learning is required."
    d = run(job=make_job(description=desc))
    assert d.decision == "SKIP" and "research_credentials_required" in d.reasons


@pytest.mark.parametrize("title", ["Junior ML Engineer", "HealthTech Backend Engineer"])
def test_selective_roles_always_reviewed(title):
    d = run(job=make_job(title=title))
    assert d.decision == "REVIEW_REQUIRED" and "selective_target_role" in d.reasons


def test_title_outside_target_families_requires_review():
    d = run(job=make_job(title="Software Engineer"))
    assert d.decision == "REVIEW_REQUIRED" and "title_outside_target_families" in d.reasons


@pytest.mark.parametrize("title", ["Generative AI Engineer", "Fraud Engineer", "Payments Engineer", "Django Developer"])
def test_target_family_titles_accepted(title):
    d = run(job=make_job(title=title))
    assert "title_outside_target_families" not in d.reasons


# ---------- Location / employment ----------

@pytest.mark.parametrize("loc", ["Hyderabad", "Bangalore", "Bengaluru, Hybrid", "Pune", "Chennai", "Remote - India", "Hybrid - Hyderabad"])
def test_preferred_locations_accepted(loc):
    d = run(job=make_job(location=loc))
    assert d.decision == "ELIGIBLE_FOR_LOCAL_DRAFT", d.reasons


@pytest.mark.parametrize("loc", ["Mumbai", "Gurugram", "Remote - US", "London"])
def test_other_locations_skipped(loc):
    d = run(job=make_job(location=loc))
    assert d.decision == "SKIP" and "location_outside_preferences" in d.reasons


def test_missing_location_requires_review():
    d = run(job=make_job(location=None))
    assert d.decision == "REVIEW_REQUIRED" and "missing_location" in d.reasons


def test_remote_without_country_requires_review():
    d = run(job=make_job(location="Remote"))
    assert d.decision == "REVIEW_REQUIRED" and "remote_country_unspecified" in d.reasons


def test_contract_skipped_unless_enabled():
    assert run(job=make_job(employment_type="Contract")).decision == "SKIP"
    d = run(job=make_job(employment_type="Contract"), profile=make_profile(allow_contract=True))
    assert d.decision == "ELIGIBLE_FOR_LOCAL_DRAFT"


def test_internship_skipped():
    d = run(job=make_job(employment_type="Internship"))
    assert d.decision == "SKIP" and "employment_type_outside_preferences" in d.reasons


def test_missing_employment_type_requires_review():
    d = run(job=make_job(employment_type=None))
    assert d.decision == "REVIEW_REQUIRED" and "missing_employment_type" in d.reasons


def test_hard_skip_wins_over_review_reasons():
    d = run(job=make_job(title="Senior Python Developer", description=None), approval=None)
    assert d.decision == "SKIP"
