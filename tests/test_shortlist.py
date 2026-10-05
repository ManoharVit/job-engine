import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from src.models.base import Base
from src.review.models import ReviewRecord
from src.models.audit import AuditLog

from src.api.main import app, get_db
from src.models.profile import CandidateProfile, Evidence

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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_shortlist_api_parse_and_build(client):
    profile = CandidateProfile(
        target_roles=["Backend Developer"],
        must_have_skills=["Python", "FastAPI"],
    )
    evidence = [
        Evidence(skill="Python", source_type="repo", reference="github", verified=True),
        Evidence(skill="FastAPI", source_type="repo", reference="github", verified=True)
    ]
    
    payload = {
        "text": "https://example.com/job/123 | Backend Developer | TechCorp | Remote | 3 years | Python, FastAPI",
        "profile": profile.model_dump(),
        "evidence": [e.model_dump() for e in evidence],
        "profile_version": "v1",
        "allow_contract": False
    }

    response = client.post("/api/shortlist", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "counts" in data
    assert "entries" in data
    assert len(data["entries"]) == 1
    
    entry = data["entries"][0]
    assert entry["company"] == "TechCorp"
    assert entry["title"] == "Backend Developer"
    assert entry["bucket"] in ["SHORTLISTED", "NEEDS_REVIEW", "EXCLUDED"]

