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
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    review_audit_id: Optional[int] = None


class EligibilityJob(BaseModel):
    title: str
    description: Optional[str] = None
    required_skills: List[str] = []
    experience_level: Optional[str] = None
    application_status: str


def normalize_skill(skill: str) -> str:
    s = skill.lower().strip()
    aliases = {
        "nodejs": "node.js",
        "postgres": "postgresql",
        "c++": "c++",
        "reactjs": "react"
    }
    return aliases.get(s, s)

def evaluate_seniority(job_exp: Optional[str], prof_exp: Optional[str]) -> Literal["PASS", "FAIL", "UNKNOWN"]:
    if not job_exp or not prof_exp:
        return "UNKNOWN"
        
    j = job_exp.lower().strip()
    p = prof_exp.lower().strip()
    
    # Controlled vocabulary mapping
    seniority_levels = {
        "entry-level": 1,
        "junior": 1,
        "1-3 years": 1,
        "mid-level": 2,
        "3-5 years": 2,
        "senior": 3,
        "senior software engineer": 3,
        "principal": 4
    }
    
    job_level = seniority_levels.get(j)
    prof_level = seniority_levels.get(p)
    
    if job_level is None or prof_level is None:
        return "UNKNOWN"
        
    if job_level > prof_level:
        return "FAIL"
        
    return "PASS"


def evaluate_application_eligibility(
    job: EligibilityJob,
    profile: Optional[CandidateProfile],
    evidence: List[Evidence],
    verified_job: bool,
) -> Dict[str, Any]:
    """
    Evaluates whether a job is eligible for local drafting/application,
    purely based on local data.
    """
    if not profile:
        return {"decision": "SKIP_MISSING_PROFILE", "reason": "missing profile", "application_action": "not_performed"}
        
    if not job.title.strip() or not (job.description or "").strip():
        return {"decision": "SKIP_MISSING_JOB_DETAILS", "reason": "missing job details", "application_action": "not_performed"}
        
    if not verified_job:
        return {"decision": "SKIP_UNVERIFIED_JOB", "reason": "unverified job", "application_action": "not_performed"}
        
    if job.application_status != "APPROVED":
        return {"decision": "SKIP_NOT_HUMAN_APPROVED", "reason": "not approved", "application_action": "not_performed"}
        
    # Seniority mismatch
    seniority_decision = evaluate_seniority(job.experience_level, profile.experience_range)
    if seniority_decision == "FAIL":
        return {"decision": "SKIP_SENIORITY_MISMATCH", "reason": "seniority_mismatch", "application_action": "not_performed"}
    elif seniority_decision == "UNKNOWN":
        return {"decision": "REVIEW_REQUIRED", "reason": "unknown_seniority", "application_action": "not_performed"}
            
    # Missing required skills check against profile and verified evidence
    if not job.required_skills:
        return {"decision": "REVIEW_REQUIRED", "reason": "no_required_skills", "application_action": "not_performed"}

    missing_skills = []
    
    norm_must_have = {normalize_skill(s) for s in profile.must_have_skills}
    norm_nice_have = {normalize_skill(s) for s in profile.nice_to_have_skills}
    
    for req_skill in job.required_skills:
        norm_req = normalize_skill(req_skill)
        has_skill = norm_req in norm_must_have or norm_req in norm_nice_have
        if not has_skill:
            missing_skills.append(req_skill)
            continue
            
        evidence_found = False
        for ev in evidence:
            if normalize_skill(ev.skill) == norm_req and ev.verified:
                evidence_found = True
                break
                
        if not evidence_found:
            missing_skills.append(req_skill)
            
    if missing_skills:
        missing_skills.sort()
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
        
    if not evidence.verified_by or not evidence.verified_at or not evidence.review_audit_id:
        return "UNCONFIRMED"
        
    return "CONFIRMED_BY_HUMAN_REVIEW"
