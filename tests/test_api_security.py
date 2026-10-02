import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.models.job import JobModel, Draft
from src.models.audit import AuditLog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.base import Base
from src.tracking.database import get_db
from sqlalchemy.pool import StaticPool

client = TestClient(app)

@pytest.fixture(name="test_db", autouse=True)
def setup_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    def override_get_db():
        try:
            yield db
        finally:
            pass # Keep it open for tests
    app.dependency_overrides[get_db] = override_get_db
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()

def test_dry_run_no_persistence(test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "secur1", "canonical_url": "http://example.com/sec1",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]

    profile_data = {
        "profile": {},
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}
        ]
    }
    response = client.post(f"/api/jobs/{job_id}/draft?dry_run=true", json=profile_data)
    assert response.status_code == 200

    assert test_db.query(Draft).filter_by(job_id=job_id).count() == 0
    assert test_db.query(AuditLog).filter(AuditLog.details.like(f"%cover_letter%")).count() == 0

def test_real_mode_opt_in():
    import inspect
    from src.api.main import generate_draft
    sig = inspect.signature(generate_draft)
    assert sig.parameters["dry_run"].default is True

def test_review_no_mutation(test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "secur2", "canonical_url": "http://example.com/sec2",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    profile_data = {
        "profile": {"must_have_skills": ["Python"]},
        "evidence": [{"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}]
    }
    
    response = client.post(f"/api/jobs/{job_id}/review", json=profile_data)
    assert response.status_code == 200
    
    assert test_db.query(Draft).filter_by(job_id=job_id).count() == 0
    assert test_db.query(AuditLog).filter_by(entity_id=str(job_id)).count() == 0
    job = test_db.get(JobModel, job_id)
    assert job.application_status == "NEW"

def test_rejected_withdrawn_reason(test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "secur3", "canonical_url": "http://example.com/sec3",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    response = client.post(f"/api/jobs/{job_id}/state", json={"state": "REJECTED", "reason": ""})
    assert response.status_code == 400
    assert "reason is required" in response.json()["detail"]
    
def test_export_only_approved(test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "secur4", "canonical_url": "http://example.com/sec4",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 400
    assert "not APPROVED" in response.json()["detail"]

def test_no_secrets_in_errors(test_db, monkeypatch):
    import src.api.main as main_api
    def mock_create(*args, **kwargs):
        raise Exception("SuperSecretPassword")
    monkeypatch.setattr(main_api, "create_job", mock_create)
    csv_content = "source,source_job_id,canonical_url,title,company,description\nmanual,db1,http://db.com,DB,Company,Desc"
    response = client.post("/api/jobs/csv", content=csv_content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    data = response.json()
    assert "SuperSecretPassword" not in str(data)
    assert "Database error occurred saving job" in str(data)

def test_malformed_csv_row_numbers(test_db):
    # Invalid URL to trigger Pydantic validation error
    csv_content = "source,source_job_id,canonical_url,title,company,description\nmanual,edge_2,invalid_url,Title,Company,Desc"
    response = client.post("/api/jobs/csv", content=csv_content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 0
    assert len(data["rejected"]) == 1
    assert data["rejected"][0]["row_number"] == 2
