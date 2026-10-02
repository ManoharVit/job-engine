from datetime import datetime, UTC
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text, ForeignKey, UniqueConstraint
from src.models.base import Base, JSONList
import json

class Draft(Base):
    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    document_type = Column(String, nullable=False)
    version = Column(Integer, default=1)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('job_id', 'document_type', 'version', name='_job_doc_version_uc'),
    )

class JobModel(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, nullable=False)
    source_job_id = Column(String, nullable=False, unique=True)
    canonical_url = Column(String, nullable=False)
    title = Column(String, nullable=False)
    company = Column(String, nullable=False)
    location = Column(String, nullable=True)
    remote_status = Column(String, nullable=True)
    employment_type = Column(String, nullable=True)
    salary_text = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    required_skills = Column(JSONList, nullable=True)
    preferred_skills = Column(JSONList, nullable=True)
    experience_level = Column(String, nullable=True)
    posted_at = Column(DateTime, nullable=True)
    discovered_at = Column(DateTime, default=lambda: datetime.now(UTC))
    application_status = Column(String, default="NEW")
    fit_score = Column(Float, nullable=True)
    fit_reasons = Column(Text, nullable=True)
    missing_skills = Column(JSONList, nullable=True)
    evidence_links = Column(JSONList, nullable=True)
    notes = Column(Text, nullable=True)

class JobCreate(BaseModel):
    source: str
    source_job_id: str
    canonical_url: HttpUrl
    title: str
    company: str
    location: Optional[str] = None
    remote_status: Optional[str] = None
    employment_type: Optional[str] = None
    salary_text: Optional[str] = None
    description: Optional[str] = None
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    experience_level: Optional[str] = None
    posted_at: Optional[datetime] = None

class JobRead(JobCreate):
    id: int
    discovered_at: datetime
    application_status: str
    fit_score: Optional[float] = None
    fit_reasons: Optional[str] = None
    missing_skills: List[str] = Field(default_factory=list)
    evidence_links: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
