from src.models.job import JobCreate
from src.tracking.crud import create_job, get_job, get_job_by_source_id

def test_create_and_get_job(db_session):
    job_data = JobCreate(
        source="test",
        source_job_id="test1",
        canonical_url="https://example.com/1",
        title="Backend Engineer",
        company="Startup Inc",
        required_skills=["Python", "FastAPI"]
    )
    
    job = create_job(db_session, job_data)
    assert job.id is not None
    assert job.title == "Backend Engineer"
    assert job.required_skills == ["Python", "FastAPI"]
    
    fetched = get_job(db_session, job.id)
    assert fetched.id == job.id
    assert fetched.source_job_id == "test1"
    
    # Test Pydantic model validation from ORM
    from src.models.job import JobRead
    job_read = JobRead.model_validate(fetched)
    assert job_read.required_skills == ["Python", "FastAPI"]
    assert job_read.missing_skills == []

def test_get_job_by_source_id(db_session):
    job_data = JobCreate(
        source="test",
        source_job_id="test2",
        canonical_url="https://example.com/2",
        title="ML Engineer",
        company="AI Corp"
    )
    create_job(db_session, job_data)
    
    fetched = get_job_by_source_id(db_session, "test2")
    assert fetched is not None
    assert fetched.title == "ML Engineer"
    
    not_found = get_job_by_source_id(db_session, "nonexistent")
    assert not_found is None
