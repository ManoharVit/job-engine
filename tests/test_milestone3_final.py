import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from src.models.base import Base
from src.models.job import JobModel, Draft
from src.models.profile import CandidateProfile, Evidence
from src.models.audit import AuditLog
from src.document_generation import DocumentGenerator, DuplicateDraftError
from src.approval import ApprovalStateMachine, JobState, InvalidStateTransitionError

def test_fresh_database_initialization():
    """Requirement 2 & 9: Create DB from fresh metadata and verify drafts table exists."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "drafts" in tables, "Drafts table was not created by Base.metadata.create_all"

def test_failed_draft_insert_leaves_no_audit_event(db_session):
    """Requirement 4: Failed draft insert leaves no misleading audit event."""
    # Setup job
    job = JobModel(
        source="test",
        source_job_id="final1",
        canonical_url="http://test.com",
        title="Software Engineer",
        company="TechCorp",
        required_skills=["Python", "SQL"],
        application_status="REVIEWED"
    )
    db_session.add(job)
    db_session.commit()
    
    evidence = Evidence(skill="Python", verified=True, source_type="resume", reference="ref")
    
    gen = DocumentGenerator(db_session, dry_run=False)
    
    # First draft should succeed
    gen.generate_cover_letter_draft(job, None, [evidence])
    
    initial_audit_count = db_session.query(AuditLog).count()
    initial_draft_count = db_session.query(Draft).count()
    
    # Second draft for the same job and version should fail
    with pytest.raises(DuplicateDraftError):
        gen.generate_cover_letter_draft(job, None, [evidence])
        
    final_audit_count = db_session.query(AuditLog).count()
    final_draft_count = db_session.query(Draft).count()
    
    assert final_audit_count == initial_audit_count, "An audit event was logged despite the draft generation failing"
    assert final_draft_count == initial_draft_count, "A draft was inserted despite the failure"


def test_terminal_and_post_approval_transitions(db_session):
    """Requirement 8: Tests for terminal and post-approval transitions."""
    machine = ApprovalStateMachine(db_session)
    
    # Test APPROVED -> APPLIED -> REJECTED
    job = JobModel(
        source="test",
        source_job_id="final2",
        canonical_url="http://test.com",
        title="SE",
        company="Corp",
        application_status="APPROVED"
    )
    db_session.add(job)
    db_session.commit()
    
    # Must have a draft to move to APPLIED
    draft = Draft(job_id=job.id, document_type="cover_letter", version=1, content="Test")
    db_session.add(draft)
    db_session.commit()
    
    job, _ = machine.transition(job, JobState.APPLIED)
    assert job.application_status == "APPLIED"
    
    # Post-application rejection
    job, _ = machine.transition(job, JobState.REJECTED, reason="Company rejected")
    assert job.application_status == "REJECTED"
    
    # Test APPLIED -> WITHDRAWN
    job2 = JobModel(
        source="test",
        source_job_id="final3",
        canonical_url="http://test.com",
        title="SE 2",
        company="Corp 2",
        application_status="APPROVED"
    )
    db_session.add(job2)
    db_session.commit()
    draft2 = Draft(job_id=job2.id, document_type="cover_letter", version=1, content="Test2")
    db_session.add(draft2)
    db_session.commit()
    
    job2, _ = machine.transition(job2, JobState.APPLIED)
    job2, _ = machine.transition(job2, JobState.WITHDRAWN, reason="Accepted another offer")
    assert job2.application_status == "WITHDRAWN"
    
    # Test APPROVED -> REJECTED and APPROVED -> WITHDRAWN
    job3 = JobModel(
        source="test",
        source_job_id="final4",
        canonical_url="http://test.com",
        title="SE 3",
        company="Corp 3",
        application_status="APPROVED"
    )
    db_session.add(job3)
    db_session.commit()
    job3, _ = machine.transition(job3, JobState.REJECTED, reason="Salary too low")
    assert job3.application_status == "REJECTED"

    job4 = JobModel(
        source="test",
        source_job_id="final5",
        canonical_url="http://test.com",
        title="SE 4",
        company="Corp 4",
        application_status="APPROVED"
    )
    db_session.add(job4)
    db_session.commit()
    job4, _ = machine.transition(job4, JobState.WITHDRAWN, reason="Lost interest")
    assert job4.application_status == "WITHDRAWN"
    
    # Test REJECTED is terminal
    with pytest.raises(InvalidStateTransitionError):
        machine.transition(job, JobState.WITHDRAWN)
        
    with pytest.raises(InvalidStateTransitionError):
        machine.transition(job2, JobState.REJECTED)

def test_commit_failure_rolls_back_audit(db_session, monkeypatch):
    """Requirement 4: Ensure atomicity if commit fails"""
    job = JobModel(
        source="test",
        source_job_id="final_mock_commit",
        canonical_url="http://test.com",
        title="Software Engineer",
        company="TechCorp",
        required_skills=["Python"],
        application_status="REVIEWED"
    )
    db_session.add(job)
    db_session.commit()
    
    evidence = Evidence(skill="Python", verified=True, source_type="resume", reference="ref")
    
    gen = DocumentGenerator(db_session, dry_run=False)
    
    original_commit = db_session.commit
    def mock_commit():
        raise Exception("Simulated failure")
        
    monkeypatch.setattr(db_session, "commit", mock_commit)
    
    with pytest.raises(Exception, match="Simulated failure"):
        gen.generate_cover_letter_draft(job, None, [evidence])
        
    # Session rollback should have cleared pending objects
    assert len(db_session.new) == 0
