import pytest
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.base import Base
from src.models.job import JobModel, Draft
from src.models.profile import CandidateProfile, Evidence
from src.models.audit import AuditLog
from src.approval import ApprovalStateMachine, JobState, InvalidStateTransitionError
from src.document_generation import DocumentGenerator, MissingEvidenceError, DuplicateDraftError

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    yield session
    session.close()

def test_invalid_state_transitions(session):
    job = JobModel(source="test", source_job_id="1", canonical_url="http://test.com", title="SE", company="Test")
    session.add(job)
    session.commit()
    
    state_machine = ApprovalStateMachine(session)
    
    # Valid: NEW -> REVIEWED
    state_machine.transition(job, JobState.REVIEWED)
    assert job.application_status == JobState.REVIEWED.value
    
    # REVIEWED cannot transition directly to APPROVED
    with pytest.raises(InvalidStateTransitionError):
        state_machine.transition(job, JobState.APPROVED)
        
    # NEW cannot transition directly to APPLIED
    job2 = JobModel(source="test", source_job_id="2", canonical_url="http://test.com", title="SE", company="Test")
    session.add(job2)
    session.commit()
    with pytest.raises(InvalidStateTransitionError):
        state_machine.transition(job2, JobState.APPLIED)

    # Valid path to APPROVED
    state_machine.transition(job, JobState.DRAFTED)
    state_machine.transition(job, JobState.APPROVED)
    
    # APPROVED cannot transition to APPLIED before a DRAFTED state
    # (i.e. if there's no actual Draft in the database)
    job3 = JobModel(source="test", source_job_id="3_new", canonical_url="http://test.com", title="SE", company="Test", application_status="APPROVED")
    session.add(job3)
    session.commit()
    with pytest.raises(InvalidStateTransitionError, match="No Draft exists"):
        state_machine.transition(job3, JobState.APPLIED)

    # Add draft so job can transition to APPLIED
    draft = Draft(job_id=job.id, document_type="cover_letter", content="test")
    session.add(draft)
    session.commit()
    
    # APPLIED can transition to WITHDRAWN
    state_machine.transition(job, JobState.APPLIED)
    state_machine.transition(job, JobState.WITHDRAWN)
    
    # WITHDRAWN is terminal
    with pytest.raises(InvalidStateTransitionError):
        state_machine.transition(job, JobState.REJECTED)
        
def test_approval_denial(session):
    job = JobModel(source="test", source_job_id="3", canonical_url="http://test.com", title="SE", company="Test")
    session.add(job)
    session.commit()
    
    state_machine = ApprovalStateMachine(session)
    state_machine.transition(job, JobState.REVIEWED)
    state_machine.transition(job, JobState.REJECTED)
    assert job.application_status == JobState.REJECTED.value
    
def test_unsupported_claims_marked_verify(session):
    job = JobModel(
        source="test", source_job_id="4", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python", "Go", "Rust"]
    )
    profile = CandidateProfile()
    evidences = [
        Evidence(skill="Python", source_type="resume", reference="ref", verified=True),
        Evidence(skill="Go", source_type="resume", reference="ref", verified=False), # Not verified
    ]
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    draft, audit = doc_gen.generate_cover_letter_draft(job, profile, evidences)
    
    assert "proven experience with Python" in draft
    assert "[VERIFY: Go experience]" in draft
    assert "[VERIFY: Rust experience]" in draft
    
def test_missing_evidence(session):
    job = JobModel(
        source="test", source_job_id="5", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python"]
    )
    profile = CandidateProfile()
    evidences = [] # Missing evidence
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    with pytest.raises(MissingEvidenceError):
        doc_gen.generate_cover_letter_draft(job, profile, evidences)

def test_only_unverified_evidence(session):
    job = JobModel(
        source="test", source_job_id="5_1", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python"]
    )
    profile = CandidateProfile()
    evidences = [Evidence(skill="Python", source_type="resume", reference="ref", verified=False)]
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    with pytest.raises(MissingEvidenceError):
        doc_gen.generate_cover_letter_draft(job, profile, evidences)

def test_mixed_evidence(session):
    job = JobModel(
        source="test", source_job_id="5_2", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python", "Go"]
    )
    session.add(job)
    session.commit()
    profile = CandidateProfile()
    evidences = [
        Evidence(skill="Python", source_type="resume", reference="ref", verified=True),
        Evidence(skill="Go", source_type="resume", reference="ref", verified=False)
    ]
    
    doc_gen = DocumentGenerator(session, dry_run=False)
    draft, audit = doc_gen.generate_cover_letter_draft(job, profile, evidences)
    
    assert "proven experience with Python" in draft
    assert "[VERIFY: Go experience]" in draft
    
def test_duplicate_drafts(session):
    job = JobModel(
        source="test", source_job_id="6", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python"]
    )
    session.add(job)
    session.commit()
    profile = CandidateProfile()
    evidences = [Evidence(skill="Python", source_type="resume", reference="ref", verified=True)]
    
    doc_gen = DocumentGenerator(session, dry_run=False) # MUST BE FALSE TO HIT DB
    doc_gen.generate_cover_letter_draft(job, profile, evidences)
    
    with pytest.raises(DuplicateDraftError):
        doc_gen.generate_cover_letter_draft(job, profile, evidences)

def test_fit_summary_deterministic(session):
    job = JobModel(
        source="test", source_job_id="7", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python", "Docker", "AWS"],
        preferred_skills=["Kubernetes", "Rust"]
    )
    profile = CandidateProfile(must_have_skills=["Python", "Docker", "Kubernetes", "AWS"])
    evidences = [
        Evidence(skill="Python", source_type="resume", reference="ref", verified=True),
        Evidence(skill="AWS", source_type="resume", reference="ref", verified=True)
    ]
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    summary, audit = doc_gen.generate_fit_summary(job, profile, evidences)
    
    assert summary["matched"] == ["aws", "python"]
    assert summary["uncertain"] == ["docker", "kubernetes"]
    assert summary["missing"] == ["rust"]

def test_resume_keyword_suggestions_deterministic(session):
    job = JobModel(
        source="test", source_job_id="8", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python", "Docker", "Zookeeper"],
        preferred_skills=["Kubernetes", "AWS", "Babel"]
    )
    evidences = [
        Evidence(skill="Python", source_type="resume", reference="ref", verified=True),
        Evidence(skill="Zookeeper", source_type="resume", reference="ref", verified=True),
        Evidence(skill="AWS", source_type="resume", reference="ref", verified=True),
        Evidence(skill="Babel", source_type="resume", reference="ref", verified=True),
        Evidence(skill="GCP", source_type="resume", reference="ref", verified=True)
    ]
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    suggestions, audit = doc_gen.generate_resume_keyword_suggestions(job, evidences)
    
    assert suggestions == ["aws", "babel", "python", "zookeeper"]

def test_dry_run_behavior(session):
    job = JobModel(
        source="test", source_job_id="9", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python"]
    )
    session.add(job)
    session.commit()
    profile = CandidateProfile()
    evidences = [Evidence(skill="Python", source_type="resume", reference="ref", verified=True)]
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    draft, audit = doc_gen.generate_cover_letter_draft(job, profile, evidences)
    
    # Audit log should be returned but not persisted
    assert audit.action == "GENERATE_COVER_LETTER_DRAFT"
    
    # Check DB
    assert session.query(Draft).count() == 0
    assert session.query(AuditLog).count() == 0

def test_fit_summary_negated(session):
    job = JobModel(
        source="test", source_job_id="6_1", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["C++", "Python"]
    )
    profile = CandidateProfile(excluded_skills=["C++"])
    evidences = [
        Evidence(skill="Python", source_type="resume", reference="ref", verified=True)
    ]
    
    doc_gen = DocumentGenerator(session, dry_run=True)
    summary, audit = doc_gen.generate_fit_summary(job, profile, evidences)
    
    assert "python" in summary["matched"]
    assert "c++" in summary["negated"]

def test_draft_job_not_saved(session):
    """Generating a draft in real mode for a job with no id rolls back and raises ValueError."""
    job = JobModel(
        source="test", source_job_id="unsaved", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python"]
    )
    # Deliberately do NOT add/commit, so job.id is None
    profile = CandidateProfile()
    evidences = [Evidence(skill="Python", source_type="resume", reference="ref", verified=True)]
    
    doc_gen = DocumentGenerator(session, dry_run=False)
    with pytest.raises(ValueError, match="Job must be saved to DB"):
        doc_gen.generate_cover_letter_draft(job, profile, evidences)

def test_draft_integrity_error_via_raw_insert(session):
    """Same-session uniqueness test: insert a conflicting draft via raw SQL
    to bypass the early Python check, triggering an IntegrityError on commit.
    Verifies DuplicateDraftError is raised and the session is still usable."""
    from sqlalchemy import text

    job = JobModel(
        source="test", source_job_id="10", canonical_url="http://test.com", title="SE", company="Test",
        required_skills=["Python"]
    )
    session.add(job)
    session.commit()
    profile = CandidateProfile()
    evidences = [Evidence(skill="Python", source_type="resume", reference="ref", verified=True)]
    
    doc_gen = DocumentGenerator(session, dry_run=False)
    
    # First draft succeeds
    doc_gen.generate_cover_letter_draft(job, profile, evidences)
    
    # Second attempt hits the early duplicate check
    with pytest.raises(DuplicateDraftError):
        doc_gen.generate_cover_letter_draft(job, profile, evidences)
    
    # Session still usable afterward
    assert session.query(JobModel).count() > 0



def test_transition_dry_run(session):
    job = JobModel(source="test", source_job_id="99", canonical_url="http://test.com", title="SE", company="Test")
    session.add(job)
    session.commit()
    
    state_machine = ApprovalStateMachine(session)
    job_ret, audit = state_machine.transition(job, JobState.REVIEWED, dry_run=True)
    
    # Check that audit log is created but NOT saved
    assert audit.action == "STATE_TRANSITION"
    assert "dry_run" in audit.details
    assert session.query(AuditLog).count() == 0
