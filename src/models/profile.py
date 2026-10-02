from typing import List, Optional
from pydantic import BaseModel, Field

class Evidence(BaseModel):
    skill: str
    source_type: str # e.g. "resume", "project", "repo"
    reference: str # URL or pointer to the evidence
    verified: bool = False

class CandidateProfile(BaseModel):
    target_roles: List[str] = Field(default_factory=list)
    preferred_locations: List[str] = Field(default_factory=list)
    remote_preference: str = "hybrid"
    experience_range: str = "0-3 years"
    salary_preference: Optional[str] = None
    must_have_skills: List[str] = Field(default_factory=list)
    nice_to_have_skills: List[str] = Field(default_factory=list)
    excluded_skills: List[str] = Field(default_factory=list)
    excluded_companies: List[str] = Field(default_factory=list)
    excluded_roles: List[str] = Field(default_factory=list)
    work_authorization: str = "Authorized"
    resume_versions: List[str] = Field(default_factory=list)
    evidence_registry: List[Evidence] = Field(default_factory=list)
