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
    assert "fit_score" in data
    assert "fit_score_breakdown" in data
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

def test_deterministic_output(client, test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "det1", "canonical_url": "http://example.com/det",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python", "Docker"]
    })
    job_id = res.json()["id"]
    
    profile_data = {
        "profile": {
            "must_have_skills": ["Python"]
        },
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}
        ]
    }
    
    response1 = client.post(f"/api/jobs/{job_id}/review", json=profile_data)
    response2 = client.post(f"/api/jobs/{job_id}/review", json=profile_data)
    
    assert response1.json() == response2.json()

def test_database_rollback(client, test_db, monkeypatch):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "roll1", "canonical_url": "http://example.com/roll",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    client.post(f"/api/jobs/{job_id}/state", json={"state": "REVIEWED", "reason": "ok", "dry_run": False})
    
    profile_data = {
        "profile": {},
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}
        ]
    }
    
    # We will simulate a failure during the DB commit in generate_cover_letter_draft
    # Since dry_run=False, it will try to write Draft and AuditLog.
    # Let's mock the session commit to raise an error
    
    def mock_commit():
        raise Exception("DB failure")
        
    original_commit = test_db.commit
    monkeypatch.setattr(test_db, "commit", mock_commit)
    
    try:
        response = client.post(f"/api/jobs/{job_id}/draft?dry_run=false", json=profile_data)
        assert response.status_code == 500
    except Exception as e:
        assert str(e) == "DB failure"
        
    # Restore commit
    monkeypatch.setattr(test_db, "commit", original_commit)
    
    # Check that there's no Draft in the DB
    from src.models.job import Draft
    drafts = test_db.query(Draft).filter_by(job_id=job_id).all()
    assert len(drafts) == 0

def test_csv_edge_cases(client, test_db):
    # Misquoted multiline description and omitted skill columns
    csv_content = """source,source_job_id,canonical_url,title,company,description
manual,edge_1,https://example.com/edge,Title,Company,"This is a 
multiline description"
manual,edge_2,https://example.com/edge2,Title2,,Desc"""
    response = client.post("/api/jobs/csv", content=csv_content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    data = response.json()
    assert data["imported"] == 2
    assert len(data["rejected"]) == 0
    
def test_draft_missing_evidence(client, test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "draftmiss", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    
    # Missing evidence should fail
    profile_data_no_evidence = {
        "profile": {},
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": False}
        ]
    }
    response = client.post(f"/api/jobs/{job_id}/draft?dry_run=true", json=profile_data_no_evidence)
    assert response.status_code == 400
    assert "No verified evidence" in response.json()["detail"]

def test_draft_duplicate(client, test_db):
    res = client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "draftdup", "canonical_url": "http://example.com",
        "title": "Engineer", "company": "Corp", "required_skills": ["Python"]
    })
    job_id = res.json()["id"]
    client.post(f"/api/jobs/{job_id}/state", json={"state": "REVIEWED", "reason": "ok", "dry_run": False})
    
    profile_data = {
        "profile": {},
        "evidence": [
            {"skill": "Python", "source_type": "resume", "reference": "ref", "verified": True}
        ]
    }
    
    response = client.post(f"/api/jobs/{job_id}/draft?dry_run=false", json=profile_data)
    assert response.status_code == 200
    
    response2 = client.post(f"/api/jobs/{job_id}/draft?dry_run=false", json=profile_data)
    assert response2.status_code == 400
    assert "Draft already generated" in response2.json()["detail"]

def test_job_not_saved_draft_real_mode(client, test_db, monkeypatch):
    # Testing ValueError: job.id is None in real mode
    from src.models.job import JobModel
    from src.models.profile import CandidateProfile, Evidence
    from src.document_generation import DocumentGenerator
    
    job = JobModel(title="T", company="C", source="m")
    gen = DocumentGenerator(test_db, dry_run=False)
    try:
        gen.generate_cover_letter_draft(job, CandidateProfile(must_have_skills=[]), [Evidence(skill="a", source_type="resume", reference="ref", verified=True)])
        assert False
    except ValueError as e:
        assert "Job must be saved to DB" in str(e)

def test_api_404s(client):
    assert client.get("/api/jobs/999").status_code == 404
    assert client.post("/api/jobs/999/review", json={"profile":{}, "evidence":[]}).status_code == 404
    assert client.post("/api/jobs/999/draft", json={"profile":{}, "evidence":[]}).status_code == 404
    assert client.post("/api/jobs/999/state", json={"state":"APPROVED", "reason":"ok"}).status_code == 404
    assert client.get("/api/jobs/999/export").status_code == 404

def test_csv_import_duplicate(client, test_db):
    # Insert first to make existing
    client.post("/api/jobs", json={
        "source": "manual", "source_job_id": "dup1", "canonical_url": "http://dup.com",
        "title": "Dup", "company": "Company", "required_skills": []
    })
    csv_content = """source,source_job_id,canonical_url,title,company,description\nmanual,dup2,http://dup.com,Dup,Company,Desc"""
    response = client.post("/api/jobs/csv", content=csv_content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    assert response.json()["imported"] == 0
    assert len(response.json()["rejected"]) == 1

def test_csv_import_db_error(client, test_db, monkeypatch):
    import src.api.main as main_api
    def mock_create(*args, **kwargs):
        raise Exception("DB Error")
    monkeypatch.setattr(main_api, "create_job", mock_create)
    csv_content = """source,source_job_id,canonical_url,title,company,description\nmanual,db1,http://db.com,DB,Company,Desc"""
    response = client.post("/api/jobs/csv", content=csv_content, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    assert response.json()["imported"] == 0
    assert "DB Error" in response.json()["rejected"][0]["error"]
