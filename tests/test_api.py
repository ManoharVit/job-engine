import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.base import Base
from src.models.job import JobModel
import json

from src.api.main import app, get_db

from sqlalchemy.pool import StaticPool

@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function")
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass
            
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_manual_job_import(client):
    payload = {
        "source": "manual",
        "source_job_id": "job_123",
        "canonical_url": "https://example.com/job/123",
        "title": "Software Engineer",
        "company": "Tech Corp",
        "required_skills": ["Python", "FastAPI"]
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Software Engineer"
    assert data["id"] is not None

def test_csv_job_import(client):
    csv_content = "source,source_job_id,canonical_url,title,company,required_skills\nmanual,job_csv_1,https://example.com,Backend Dev,CSV Corp,Python;SQL"
    response = client.post("/api/jobs/csv", content=csv_content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 1

def test_job_listing_and_filtering(client, test_db):
    # Add a couple of jobs
    client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "j1", "canonical_url": "http://example.com",
        "title": "Data Scientist", "company": "Data Corp"
    })
    client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "j2", "canonical_url": "http://example.com",
        "title": "Software Engineer", "company": "Tech Corp"
    })
    
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert len(response.json()) == 2
    
    response = client.get("/api/jobs?company=Tech Corp")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Software Engineer"

def test_review_job_endpoint(client, test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "rev1", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python", "Java"]
    })
    job_id = res.json()["id"]
    
    # Send a dummy profile to get a review
    profile_data = {
        "profile": {
            "must_have_skills": ["Python"]
        },
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}
        ]
    }
    
    response = client.post(f"/api/jobs/{job_id}/review", json=profile_data)
    assert response.status_code == 200
    data = response.json()
    assert "fit_summary" in data
    assert "python" in data["fit_summary"]["matched"]
    assert "java" in data["fit_summary"]["missing"]

def test_generate_draft_dry_run(client):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "draft1", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    profile_data = {
        "profile": {},
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}
        ]
    }
    # Default is dry_run = True
    response = client.post(f"/api/jobs/{job_id}/draft", json=profile_data)
    assert response.status_code == 200
    assert "draft" in response.json()
    assert "Python" in response.json()["draft"]
    
    # Check that it didn't persist (since we don't have an endpoint for drafts, we can just assert state is NEW)
    job_res = client.get(f"/api/jobs/{job_id}")
    assert job_res.json()["application_status"] == "NEW"

def test_generate_draft_real_and_missing_evidence(client, test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "draft2", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    # Change state to REVIEWED
    client.post(f"/api/jobs/{job_id}/state", json={"state": "REVIEWED", "reason": "ok", "dry_run": False})
    
    profile_data_no_evidence = {
        "profile": {},
        "evidence": []
    }
    # Should fail due to missing evidence
    response = client.post(f"/api/jobs/{job_id}/draft?dry_run=false", json=profile_data_no_evidence)
    assert response.status_code == 400
    assert "No verified evidence" in response.json()["detail"]
    
    # Provide evidence and succeed
    profile_data_ok = {
        "profile": {},
        "evidence": [{"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}]
    }
    response = client.post(f"/api/jobs/{job_id}/draft?dry_run=false", json=profile_data_ok)
    assert response.status_code == 200
    assert "draft" in response.json()

def test_invalid_state_transition(client):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "state1", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp"
    })
    job_id = res.json()["id"]
    
    # NEW -> APPROVED is invalid
    response = client.post(f"/api/jobs/{job_id}/state", json={"state": "APPROVED", "reason": "jump", "dry_run": False})
    assert response.status_code == 400
    assert "Cannot transition from" in response.json()["detail"]

def test_export_approved_application(client, test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "exp1", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    client.post(f"/api/jobs/{job_id}/state", json={"state": "REVIEWED", "reason": "", "dry_run": False})
    
    profile_data_ok = {
        "profile": {},
        "evidence": [{"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}]
    }
    # Draft
    client.post(f"/api/jobs/{job_id}/draft?dry_run=false", json=profile_data_ok)
    client.post(f"/api/jobs/{job_id}/state", json={"state": "DRAFTED", "reason": "", "dry_run": False})
    
    # Approve
    client.post(f"/api/jobs/{job_id}/state", json={"state": "APPROVED", "reason": "looks good", "dry_run": False})
    
    # Export
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 200
    data = response.json()
    assert "job" in data
    assert "draft" in data

def test_export_unapproved_application(client):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "exp2", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp"
    })
    job_id = res.json()["id"]
    
    # Still NEW, export should fail
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 400
    assert "Application is not APPROVED" in response.json()["detail"]
