import pytest
from datetime import datetime, UTC
from src.models.job import JobRead
from src.models.profile import CandidateProfile, Evidence
from src.interview import generate_interview_guide

def test_generate_interview_guide():
    job = JobRead(
        id=1,
        source="test",
        source_job_id="test1",
        canonical_url="https://example.com/job1",
        title="Software Engineer",
        company="Test Co",
        required_skills=["Python", "FastAPI", "React", "Docker", "AWS"],
        discovered_at=datetime.now(UTC),
        application_status="NEW",
        experience_level="mid-level"
    )

    profile = CandidateProfile(
        must_have_skills=["Python", "Docker", "UnverifiedSkill"],
        nice_to_have_skills=["FastAPI"],
        excluded_skills=["React"]
    )

    evidence = [
        Evidence(skill="Python", source_type="repo", reference="github.com/test", verified=True),
        Evidence(skill="Docker", source_type="resume", reference="Resume V1", verified=False),
        Evidence(skill="FastAPI", source_type="resume", reference="Resume V2", verified=True),
    ]

    eligibility = {
        "decision": "REVIEW_REQUIRED",
        "reason": "unknown_seniority",
        "missing_skills": ["React", "AWS"],
        "application_action": "not_performed"
    }

    guide = generate_interview_guide(job, profile, evidence, eligibility)

    # 1. Eligibility result and reason
    assert guide.eligibility.decision == "REVIEW_REQUIRED"
    assert guide.eligibility.reason == "unknown_seniority"

    # 2. Matched verified skills with evidence references
    assert any(s.skill == "Python" and s.evidence_reference == "github.com/test" for s in guide.matched_skills)
    assert any(s.skill == "FastAPI" and s.evidence_reference == "Resume V2" for s in guide.matched_skills)

    # 3. Missing, uncertain, excluded, and unverified requirements.
    assert "React" in guide.excluded_requirements
    assert "AWS" in guide.missing_requirements
    assert "AWS" in guide.uncertain_requirements # Unknown skill
    assert "Seniority level" in guide.uncertain_requirements # Unknown seniority
    
    # Unverified requirements (claimed in profile but evidence not verified, OR just missing evidence completely)
    # Docker is claimed, required, but evidence is unverified.
    assert any("Docker" in v for v in guide.unverified_requirements)

    # 4. Likely technical interview topics based only on job requirements.
    assert any("Python" in t for t in guide.technical_topics)
    assert any("AWS" in t for t in guide.technical_topics)

    # 5. Practice questions grouped by required skill.
    assert any("Python" in q.skill for q in guide.practice_questions)

    # 6. Evidence-grounded talking points.
    assert any("github.com/test" in p for p in guide.evidence_talking_points)

    # 7. Honest gap-handling language.
    assert any("React" in g for g in guide.gap_handling_language)
    assert any("AWS" in g for g in guide.gap_handling_language)

    # 8. [VERIFY: ...] markers for unsupported statements.
    # Unverified Docker should have a VERIFY marker
    assert any("[VERIFY:" in v and "Docker" in v for v in guide.unverified_requirements)

    # 9. Local Markdown and JSON export format
    md = guide.to_markdown()
    assert "## Interview Preparation Guide" in md
    assert "github.com/test" in md
    
    # Deterministic output check
    guide2 = generate_interview_guide(job, profile, evidence, eligibility)
    assert guide.model_dump_json() == guide2.model_dump_json()
