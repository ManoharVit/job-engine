from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Body, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime

from src.models.job import JobCreate, JobRead, JobModel, Draft
from src.models.profile import CandidateProfile, Evidence
from src.tracking.database import get_db, init_db
from src.tracking.crud import create_job, get_job
from src.ingestors import CSVIngestor
from src.document_generation import DocumentGenerator, MissingEvidenceError, DuplicateDraftError
from src.approval import ApprovalStateMachine, JobState, InvalidStateTransitionError
from src.eligibility import evaluate_application_eligibility, to_eligibility_job
from src.interview import generate_interview_guide
from src.screening import ScreeningQuestion, ScreeningAnswerReview, evaluate_screening_question

class ApplicationObservation(BaseModel):
    source: Literal["naukri", "linkedin", "other"]
    job_identifier_hash: str
    observed_at: datetime
    event: Literal[
        "JOB_PAGE_OPENED",
        "APPLICATION_FORM_OPENED",
        "MANUAL_CONFIRMATION_RECORDED"
    ]
    details: dict[str, str] = Field(default_factory=dict)
    human_verified: bool = False

class ScreeningReviewRequest(BaseModel):
    questions: List[ScreeningQuestion]
    profile: CandidateProfile
    evidence: List[Evidence]

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Job Engine API", lifespan=lifespan)
from fastapi.responses import RedirectResponse

@app.get("/")
def redirect_to_ui():
    return RedirectResponse(url="/ui/")

app.mount("/ui", StaticFiles(directory="static", html=True), name="static")

class ReviewRequest(BaseModel):
    profile: CandidateProfile
    evidence: List[Evidence]

class StateTransitionRequest(BaseModel):
    state: JobState
    reason: str = ""
    dry_run: bool = False

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

@app.post("/api/jobs/{job_id}/interview-guide")
def get_interview_guide(job_id: int, request: ReviewRequest, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job_read = JobRead.model_validate(job)
    eligibility_job = to_eligibility_job(job_read)
    
    eligibility_decision = evaluate_application_eligibility(
        job=eligibility_job,
        profile=request.profile,
        evidence=request.evidence,
        verified_job=True
    )

    guide = generate_interview_guide(
        job=job_read,
        profile=request.profile,
        evidence=request.evidence,
        eligibility=eligibility_decision
    )
    
    return {
        "guide": guide.model_dump(),
        "markdown": guide.to_markdown()
    }

@app.post("/api/screening-questions/review", response_model=List[ScreeningAnswerReview])
def review_screening_questions(request: ScreeningReviewRequest):
    return [evaluate_screening_question(q, request.profile, request.evidence) for q in request.questions]

@app.post("/api/jobs/{job_id}/screening-review", response_model=List[ScreeningAnswerReview])
def review_job_screening_questions(job_id: int, request: ScreeningReviewRequest, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return [evaluate_screening_question(q, request.profile, request.evidence) for q in request.questions]

from src.review.evidence import SubmissionEvidenceRecord

class OpenReviewRequest(BaseModel):
    verified_by: str

@app.post("/api/jobs/{job_id}/open-evidence-review")
def open_evidence_review(job_id: int, request: OpenReviewRequest, db: Session = Depends(get_db)):
    from src.review.service import ReviewService
    svc = ReviewService(db)
    try:
        audit_id = svc.open_evidence_review(job_id, verified_by=request.verified_by)
        return {"review_audit_id": audit_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/jobs/{job_id}/verify-confirmed")
def verify_confirmed(job_id: int, request: SubmissionEvidenceRecord, db: Session = Depends(get_db)):
    from src.review.service import ReviewService
    svc = ReviewService(db)
    try:
        log = svc.confirm_submission(job_id, request)
        return log.to_structured_event()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

from src.shortlist import (
    ShortlistRequest, ShortlistResponse, parse_link_lines, build_shortlist,
    ShortlistEntry, FitSummary, PrepPack, ScreeningDraft, SubmissionInfo, FollowUp
)
from src.models.job import JobModel

class ShortlistPasteRequest(ShortlistRequest):
    text: str

@app.post("/api/shortlist", response_model=ShortlistResponse)
def generate_shortlist(request: ShortlistPasteRequest, db: Session = Depends(get_db)):
    # 1. Parse pasted text
    items, rejected = parse_link_lines(request.text)
    
    # 2. Convert parsed items to jobs and insert/update them in the DB to get IDs
    # (Because build_shortlist expects job_ids or queries JobModel).
    # Wait, the prompt says "build_shortlist only reads jobs". So the parsed items MUST be in the DB.
    # Alternatively, build_shortlist can evaluate jobs entirely in memory?
    # Let's check build_shortlist in src/shortlist.py:
    # `query = db.query(JobModel)`
    # It reads from JobModel. So we must persist them first!
    
    from src.tracking.crud import create_job
    from src.models.job import JobCreate
    
    job_ids = []
    for item in items:
        # Check if canonical_url already exists
        from src.canonicalize import canonicalize_url
        canonical = canonicalize_url(item["url"])
        existing = db.query(JobModel).filter(JobModel.canonical_url == canonical).first()
        if existing:
            job_ids.append(existing.id)
        else:
            try:
                item_copy = item.copy()
                item_copy["canonical_url"] = canonicalize_url(item_copy.pop("url"))
                jc = JobCreate(**item_copy)
                new_job = create_job(db, jc)
                job_ids.append(new_job.id)
            except Exception as e:
                # Silently skip on error as per requirements? Let's just rollback
                db.rollback()
                pass
    
    # 3. Create ShortlistRequest for build_shortlist
    sr = ShortlistRequest(
        profile=request.profile,
        evidence=request.evidence,
        profile_version=request.profile_version,
        allow_contract=request.allow_contract,
        job_ids=job_ids
    )
    
    # 4. Call build_shortlist
    from datetime import datetime, timezone
    return build_shortlist(db, sr, now=datetime.now(timezone.utc))
