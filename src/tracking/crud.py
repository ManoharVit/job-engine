import json
from sqlalchemy.orm import Session
from src.models.job import JobModel, JobCreate

def create_job(db: Session, job: JobCreate) -> JobModel:
    db_job = JobModel(
        source=job.source,
        source_job_id=job.source_job_id,
        canonical_url=str(job.canonical_url),
        title=job.title,
        company=job.company,
        location=job.location,
        remote_status=job.remote_status,
        employment_type=job.employment_type,
        salary_text=job.salary_text,
        description=job.description,
        required_skills=job.required_skills,
        preferred_skills=job.preferred_skills,
        experience_level=job.experience_level,
        posted_at=job.posted_at,
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job

def get_job(db: Session, job_id: int) -> JobModel:
    return db.query(JobModel).filter(JobModel.id == job_id).first()

def get_job_by_source_id(db: Session, source_job_id: str) -> JobModel:
    return db.query(JobModel).filter(JobModel.source_job_id == source_job_id).first()
