from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import datetime

from src.api.dependencies import get_db
from src.api.extension.field_matcher import match_fields
from src.models.job import Job
from src.models.profile import CandidateProfile

router = APIRouter()

# Minimal dummy profile for the heuristic matcher until we wire up the DB profile
DUMMY_PROFILE = {
    "first_name": "Giri",
    "last_name": "Manohar",
    "email": "girimanoharv@example.com",
    "phone": "9876543210",
    "location": "India",
    "linkedin": "https://linkedin.com/in/giri",
    "github": "https://github.com/ManoharVit",
    "portfolio": "https://example.com"
}

@router.post("/api/match-fields", tags=["extension"])
def api_match_fields(fields: List[Dict[str, Any]] = Body(...)):
    # Use dummy profile for now, or fetch from DB if available
    return match_fields(fields, DUMMY_PROFILE)

@router.post("/api/log-application", tags=["extension"])
def api_log_application(data: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    # Convert extension payload to Job Engine payload
    job = Job(
        source="chrome-extension",
        source_job_id=data.get("url", ""),
        canonical_url=data.get("url", ""),
        title=data.get("title", "Unknown Role")[:200],
        company=data.get("company", "Unknown Company")[:200],
        description=data.get("job_description", ""),
        application_status="NEW",
        discovered_at=datetime.datetime.utcnow()
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"status": "success", "job_id": job.id}
