from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from src.models.base import Base

class ReviewRecord(Base):
    __tablename__ = "review_records"

    job_id = Column(Integer, ForeignKey("jobs.id"), primary_key=True)
    state = Column(String, nullable=False, default="DISCOVERED")
    history_json = Column(Text, nullable=False, default="[]")
    reviewer_id = Column(String, nullable=True)
    duplicate_of = Column(Integer, nullable=True)
    export_package_json = Column(Text, nullable=True)

class SubmissionEvidenceRow(Base):
    __tablename__ = "submission_evidence"
    
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    evidence_id = Column(String, nullable=False, unique=True)
    evidence_type = Column(String, nullable=False)
    observed_at = Column(DateTime, nullable=False)
    source_reference_hash = Column(String, nullable=False)
    verified_by = Column(String, nullable=False)
    verified_at = Column(DateTime, nullable=False)
    review_audit_id = Column(String, nullable=False)
