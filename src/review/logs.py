import hashlib
import json
import re
from typing import Literal, Optional, List
from pydantic import BaseModel, Field, model_validator

def job_ref_hash(url: str) -> str:
    # URL normalization
    normalized = url.lower()
    if "?" in normalized:
        normalized = normalized.split("?")[0]
    normalized = normalized.rstrip("/")
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()

class LocalLogRecord(BaseModel):
    model_config = {"extra": "forbid"}

    actor: str
    source: str
    job_ref_hash: str
    title: str
    company: str
    profile_version: str
    from_state: str
    to_state: str
    decision: str
    reasons: List[str] = Field(default_factory=list)
    submission_claim: str
    message: str
    external_action: Literal["none"] = "none"
    evidence_record_id: Optional[str] = None
    verified_by: Optional[str] = None

    @model_validator(mode="after")
    def validate_rules(self) -> "LocalLogRecord":
        if bool(re.search(r"applied\s+" + r"successfully", self.message, re.IGNORECASE)):
            raise ValueError("Forbidden phrasing in message")
        
        if not self.job_ref_hash.startswith("sha256:"):
            raise ValueError("job_ref_hash must be a sha256 hash")

        if self.submission_claim == "confirmed_by_human_review":
            if self.to_state != "CONFIRMED_BY_HUMAN_REVIEW":
                raise ValueError("Mismatch state for confirmed claim")

        if self.to_state == "CONFIRMED_BY_HUMAN_REVIEW":
            if self.submission_claim != "confirmed_by_human_review":
                 raise ValueError("Mismatch claim for confirmed state")

        if self.actor == "system":
            if self.submission_claim == "confirmed_by_human_review" or self.to_state in ("SUBMISSION_UNCONFIRMED", "CONFIRMED_BY_HUMAN_REVIEW"):
                raise ValueError("System cannot claim submission states")

        return self

    def to_structured_event(self) -> dict:
        reason = self.reasons[0] if self.reasons else None
        
        # Format mapping
        submission_status = "NOT_ATTEMPTED"
        if self.to_state == "RESUME_UPLOAD_FAILED":
            submission_status = "SUBMISSION_UNCONFIRMED"
        elif self.to_state == "CONFIRMED_BY_HUMAN_REVIEW":
            submission_status = "CONFIRMED_BY_HUMAN_REVIEW"
        elif self.submission_claim == "unconfirmed":
            submission_status = "SUBMISSION_UNCONFIRMED"
            
        action = "not_performed"
        if self.to_state == "CONFIRMED_BY_HUMAN_REVIEW":
            action = "not_automated"
            
        res = {
            "status": self.to_state,
            "submission_status": submission_status,
            "application_action": action
        }
        if reason:
            res["reason"] = reason
        if self.to_state == "CONFIRMED_BY_HUMAN_REVIEW":
            res["confirmation_evidence_type"] = "application_status_record"
        elif self.to_state == "RESUME_UPLOAD_FAILED":
            res["confirmation_evidence"] = None
        
        return res

def export_json_schemas():
    from src.review.evidence import SubmissionEvidenceRecord
    schemas = {
        "local_log_record": LocalLogRecord.model_json_schema(),
        "submission_evidence": SubmissionEvidenceRecord.model_json_schema()
    }
    for k in schemas:
        schemas[k]["additionalProperties"] = False
    return schemas
