"""Fixtures shared by the local review test modules. No browser, no network."""
from datetime import datetime, timedelta, UTC

import pytest

from src.models.profile import Evidence
from src.review.rubric import HumanApproval, ReviewJob, ReviewProfile

LONG_DESCRIPTION = (
    "Build and maintain Python services with FastAPI and PostgreSQL for a payments "
    "risk platform. Write tests with pytest, design idempotent REST APIs, and work with "
    "SQLAlchemy models. You will collaborate with product and risk teams on alerting."
)

PAST = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def make_profile(**overrides) -> ReviewProfile:
    data = dict(
        profile_version="profile-v3-test",
        must_have_skills=["Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "pytest"],
        nice_to_have_skills=["Docker", "Django", "LLM", "RAG", "REST API", "scikit-learn"],
        excluded_skills=["Java"],
    )
    data.update(overrides)
    return ReviewProfile(**data)


def make_evidence(skills=("Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "pytest", "REST API"),
                  unverified=()) -> list[Evidence]:
    ev = [Evidence(skill=s, source_type="resume", reference=f"resume-line:{s}", verified=True) for s in skills]
    ev += [Evidence(skill=s, source_type="resume", reference=f"resume-line:{s}", verified=False) for s in unverified]
    return ev


def make_job(**overrides) -> ReviewJob:
    data = dict(
        source="naukri_reference",
        canonical_url="https://www.naukri.com/job-listings-python-backend-engineer-example-123",
        title="Python Backend Engineer (FastAPI)",
        company="Example Payments Pvt Ltd",
        location="Hyderabad",
        employment_type="Full-time",
        description=LONG_DESCRIPTION,
        required_skills=["Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "pytest"],
        experience_text="1-3 Yrs",
        source_verified=True,
    )
    data.update(overrides)
    return ReviewJob(**data)


def make_approval(**overrides) -> HumanApproval:
    data = dict(verified_by="giri", approved_at=PAST, review_audit_id="audit-1")
    data.update(overrides)
    return HumanApproval(**data)


@pytest.fixture
def profile():
    return make_profile()


@pytest.fixture
def evidence():
    return make_evidence()


@pytest.fixture
def approval():
    return make_approval()


@pytest.fixture
def past():
    return PAST


@pytest.fixture
def later():
    return PAST + timedelta(hours=2)
