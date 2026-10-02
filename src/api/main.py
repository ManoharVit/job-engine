from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Body, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from src.models.job import JobCreate, JobRead, JobModel, Draft
from src.models.profile import CandidateProfile, Evidence
from src.tracking.database import get_db, init_db
from src.tracking.crud import create_job, get_job
from src.ingestors import CSVIngestor
from src.document_generation import DocumentGenerator, MissingEvidenceError, DuplicateDraftError
from src.approval import ApprovalStateMachine, JobState, InvalidStateTransitionError

app = FastAPI(title="Job Engine API")

class ReviewRequest(BaseModel):
    profile: CandidateProfile
    evidence: List[Evidence]

class StateTransitionRequest(BaseModel):
    state: JobState
    reason: str = ""
    dry_run: bool = False

@app.on_event("startup")
def startup_event():
    init_db()

@app.post("/api/jobs", response_model=JobRead)
def import_manual_job(job: JobCreate, db: Session = Depends(get_db)):
    return create_job(db, job)

@app.post("/api/jobs/csv")
async def import_csv_jobs(csv_content: str = Body(..., media_type="text/csv"), db: Session = Depends(get_db)):
    ingestor = CSVIngestor()
    jobs, rejected = ingestor.ingest(csv_content)
    
    from src.deduplicate import deduplicate_jobs, is_duplicate
    unique_csv_jobs = deduplicate_jobs(jobs)
    existing_jobs = db.query(JobModel).all()
    
    imported_count = 0
    for job in unique_csv_jobs:
        if is_duplicate(job, existing_jobs):
            rejected.append({"row": {"title": job.title}, "error": "Duplicate job"})
            continue
        try:
            create_job(db, job)
            imported_count += 1
        except Exception as e:
            db.rollback()
            rejected.append({"row": {"title": job.title}, "error": "Database error occurred saving job"})
            
    return {"imported": imported_count, "rejected": rejected}

@app.get("/api/jobs", response_model=List[JobRead])
def list_jobs(company: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(JobModel)
    if company:
        query = query.filter(JobModel.company == company)
    return query.all()

@app.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job_endpoint(job_id: int, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/api/jobs/{job_id}/review")
def review_job(job_id: int, request: ReviewRequest, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    generator = DocumentGenerator(db, dry_run=True)
    fit_summary, _ = generator.generate_fit_summary(job, request.profile, request.evidence)
    
    from src.scoring import calculate_fit_score
    # We must construct a JobCreate to pass to calculate_fit_score
    job_create = JobCreate.model_validate(job.__dict__)
    
    # We must assign evidence registry to profile for calculate_fit_score to work
    request.profile.evidence_registry = request.evidence
    
    score, breakdown = calculate_fit_score(job_create, request.profile)
    
    return {
        "job": JobRead.model_validate(job).model_dump(),
        "fit_score": score,
        "fit_score_breakdown": breakdown,
        "fit_summary": fit_summary,
        "verified_evidence": [e.model_dump() for e in request.evidence if e.verified]
    }

@app.post("/api/jobs/{job_id}/draft")
def generate_draft(job_id: int, request: ReviewRequest, dry_run: bool = True, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    generator = DocumentGenerator(db, dry_run=dry_run)
    try:
        draft, _ = generator.generate_cover_letter_draft(job, request.profile, request.evidence)
        return {"draft": draft}
    except MissingEvidenceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DuplicateDraftError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/jobs/{job_id}/state")
def transition_state(job_id: int, request: StateTransitionRequest, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    state_machine = ApprovalStateMachine(db)
    try:
        updated_job, _ = state_machine.transition(job, request.state, reason=request.reason, dry_run=request.dry_run)
        return JobRead.model_validate(updated_job).model_dump()
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/jobs/{job_id}/export")
def export_application(job_id: int, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    if job.application_status != JobState.APPROVED.value:
        raise HTTPException(status_code=400, detail="Application is not APPROVED")
        
    draft = db.query(Draft).filter_by(job_id=job.id, document_type="cover_letter", version=1).first()
    
    return {
        "job": JobRead.model_validate(job).model_dump(),
        "draft": draft.content if draft else None,
        "profile_summary": "Export summary"
    }
