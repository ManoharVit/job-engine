import re
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, model_validator

class SubmissionEvidenceRecord(BaseModel):
    model_config = {"extra": "forbid"}

    evidence_id: str
    job_ref_hash: str
    evidence_type: Literal["platform_status_record", "confirmation_email", "explicit_platform_text"]
    observed_at: datetime
    source_reference_hash: str
    verified_by: str
    verified_at: datetime
    review_audit_id: str

    @model_validator(mode="after")
    def validate_rules(self) -> "SubmissionEvidenceRecord":
        if not self.verified_by.strip():
            raise ValueError("verified_by cannot be empty")
        
        if not self.review_audit_id.strip():
            raise ValueError("review_audit_id cannot be empty")
            
        if not self.source_reference_hash.startswith("sha256:") or len(self.source_reference_hash) != 71:
            raise ValueError("source_reference_hash must be a sha256 hash")

        if self.observed_at.tzinfo is None or self.verified_at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
            
        now = datetime.now(timezone.utc)
        if self.observed_at > now or self.verified_at > now:
            raise ValueError("timestamps cannot be in the future")
            
        if self.verified_at < self.observed_at:
            raise ValueError("verified_at must be >= observed_at")

        return self
