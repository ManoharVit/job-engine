from datetime import datetime
from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel
from src.models.job import JobRead
from src.models.profile import CandidateProfile, Evidence

EligibilityDecision = Literal[
    "ELIGIBLE_FOR_LOCAL_DRAFT",
    "REVIEW_REQUIRED",
    "SKIP_MISSING_PROFILE",
    "SKIP_MISSING_JOB_DETAILS",
    "SKIP_UNVERIFIED_JOB",
    "SKIP_SENIORITY_MISMATCH",
    "SKIP_MISSING_REQUIRED_SKILLS",
    "SKIP_NOT_HUMAN_APPROVED"
]

SubmissionStatus = Literal[
    "NOT_ATTEMPTED",
    "UNCONFIRMED",
    "CONFIRMED_BY_HUMAN_REVIEW"
]

class SubmissionEvidence(BaseModel):
    evidence_type: Literal[
        "platform_confirmation_text",
        "application_status_record",
        "confirmation_email_reference"
    ]
    observed_at: datetime
    source_reference_hash: str
    verified: bool


def evaluate_application_eligibility(
    job: JobRead,
    profile: Optional[CandidateProfile],
    evidence: List[Evidence],
    approved: bool,
    verified_job: bool,
) -> Dict[str, Any]:
    """
    Evaluates whether a job is eligible for local drafting/application,
    purely based on local data.
    """
    if not profile:
        return {"decision": "SKIP_MISSING_PROFILE", "reason": "missing profile", "application_action": "not_performed"}
        
    if not job or not job.description or not job.title:
        return {"decision": "SKIP_MISSING_JOB_DETAILS", "reason": "missing job details", "application_action": "not_performed"}
        
    if not verified_job:
        return {"decision": "SKIP_UNVERIFIED_JOB", "reason": "unverified job", "application_action": "not_performed"}
        
    if not approved:
        return {"decision": "SKIP_NOT_HUMAN_APPROVED", "reason": "not approved", "application_action": "not_performed"}
        
    # Seniority mismatch (strict check)
    if job.experience_level and profile.experience_range:
        job_exp = job.experience_level.lower()
        prof_exp = profile.experience_range.lower()
        if job_exp != prof_exp and job_exp not in prof_exp and prof_exp not in job_exp:
            return {"decision": "SKIP_SENIORITY_MISMATCH", "reason": "seniority_mismatch", "application_action": "not_performed"}
            
    # Missing required skills check against profile and verified evidence
    missing_skills = []
    for req_skill in job.required_skills:
        has_skill = req_skill in profile.must_have_skills or req_skill in profile.nice_to_have_skills
        if not has_skill:
            missing_skills.append(req_skill)
            continue
            
        evidence_found = False
        for ev in evidence:
            if ev.skill == req_skill and ev.verified:
                evidence_found = True
                break
                
        if not evidence_found:
            missing_skills.append(req_skill)
            
    if missing_skills:
        return {
            "decision": "SKIP_MISSING_REQUIRED_SKILLS",
            "reason": "missing_required_skills",
            "missing_skills": missing_skills,
            "application_action": "not_performed"
        }
        
    return {"decision": "ELIGIBLE_FOR_LOCAL_DRAFT", "reason": None, "application_action": "not_performed"}


def assess_submission_evidence(
    evidence: Optional[SubmissionEvidence],
) -> SubmissionStatus:
    """
    Determines if a submission was actually confirmed.
    """
    if not evidence:
        return "NOT_ATTEMPTED"
    
    if not evidence.verified:
        return "UNCONFIRMED"
        
    return "CONFIRMED_BY_HUMAN_REVIEW"
