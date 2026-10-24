import re
from datetime import datetime
from typing import Optional, List, Tuple
from pydantic import BaseModel
from src.models.profile import Evidence
from src.review.states import ReviewState

class HumanApproval(BaseModel):
    verified_by: str
    approved_at: datetime
    review_audit_id: str

class ReviewJob(BaseModel):
    source: str
    canonical_url: str
    title: str
    company: str
    location: Optional[str] = None
    employment_type: Optional[str] = None
    description: Optional[str] = None
    required_skills: List[str] = []
    experience_text: Optional[str] = None
    source_verified: bool = False

class ReviewProfile(BaseModel):
    profile_version: str
    must_have_skills: List[str] = []
    nice_to_have_skills: List[str] = []
    excluded_skills: List[str] = []
    allow_contract: bool = False

class Experience(BaseModel):
    min_years: Optional[int]
    max_years: Optional[int]

def parse_experience(text: Optional[str], description: Optional[str], title: str) -> Experience:
    best_min, best_max = None, None
    for s in [text, description]:
        if not s: continue
        s = s.lower()
        
        min_y, max_y = None, None
        m = re.search(r"(\d+)\s*(?:-|to)\s*(\d+)\s*y", s)
        if m:
            min_y, max_y = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(\d+)\s*\+\s*y", s)
            if m:
                min_y = int(m.group(1))
            else:
                m = re.search(r"(?:minimum|at least)\s*(\d+)\s*y", s)
                if m:
                    min_y = int(m.group(1))
                else:
                    m = re.search(r"(\d+)\s*y", s)
                    if m:
                        min_y = int(m.group(1))
        
        if min_y is not None:
            if best_min is None or min_y > best_min:
                best_min = min_y
                best_max = max_y
                
    return Experience(min_years=best_min, max_years=best_max)

class EvaluationDecision(BaseModel):
    decision: str
    target_state: ReviewState
    external_action: str = "none"
    missing_skills: Tuple[str, ...] = ()
    unverified_skills: Tuple[str, ...] = ()
    priority: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

ALIAS_MAP = {"postgres": "postgresql"}

def _eval(job, profile, evidence, approval) -> EvaluationDecision:
    if not profile:
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("missing_candidate_profile",))
    
    if evidence is None or len(evidence) == 0:
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("missing_candidate_evidence",))
        
    verified = [ALIAS_MAP.get(e.skill.lower(), e.skill.lower()) for e in evidence if e.verified]
    unverified = [ALIAS_MAP.get(e.skill.lower(), e.skill.lower()) for e in evidence if not e.verified]
    
    if len(verified) == 0:
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("missing_candidate_evidence",))

    if not job.title or not job.title.strip():
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("missing_title",))
    
    if not job.company or not job.company.strip():
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("missing_company",))

    senior_titles = ["senior", "sr.", "sr", "lead", "principal", "staff", "manager", "director", "head", "architect"]
    title_words = set(re.findall(r"\b\w+\.?\b", job.title.lower()))
    for st in senior_titles:
        if st in title_words:
            return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=(f"title_seniority_excluded:{st}",))

    exp = parse_experience(job.experience_text, job.description, job.title)
    if exp.min_years is not None and exp.min_years >= 5:
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("experience_min_years_ge_5",))

    req_skills_normalized = [ALIAS_MAP.get(rs.lower(), rs.lower()) for rs in job.required_skills]
    
    for xs in profile.excluded_skills:
        if ALIAS_MAP.get(xs.lower(), xs.lower()) in req_skills_normalized:
            return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("excluded_skill_conflict",))

    profile_skills_lower = [ALIAS_MAP.get(s.lower(), s.lower()) for s in profile.must_have_skills + profile.nice_to_have_skills]
    missing_s = []
    unverified_s = []
    verified_req_count = 0
    for rs, rsl in zip(job.required_skills, req_skills_normalized):
        if rsl in ["spark", "airflow", "dbt", "kafka"] and rsl not in verified:
            missing_s.append(rs)
        elif rsl not in profile_skills_lower:
            missing_s.append(rs)
        elif rsl not in verified:
            unverified_s.append(rs)
        else:
            verified_req_count += 1
            
    if missing_s:
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("missing_required_skills",), missing_skills=tuple(missing_s))

    primary_stacks = ["java", ".net", "php", "c#"]
    for stack in primary_stacks:
        if job.title.lower().startswith(stack) or f" {stack} " in f" {job.title.lower()} ":
            return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=(f"primary_stack_excluded:{stack}",))

    weak_fit = ["data analyst", "bi developer", "power bi developer", "tableau developer"]
    if any(w in job.title.lower() for w in weak_fit):
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("weak_fit_title",))

    if "research scientist" in job.title.lower():
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("research_role",))
        
    if job.description and "phd" in job.description.lower():
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("research_credentials_required",))

    allowed_locs = ["hyderabad", "bangalore", "bengaluru", "pune", "chennai", "remote - india", "hybrid - hyderabad", "hybrid"]
    if job.location and job.location.lower() == "remote":
        pass
    elif job.location:
        loc_l = job.location.lower()
        if not any(al in loc_l for al in allowed_locs):
            return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("location_outside_preferences",))
            
    if job.employment_type and job.employment_type.lower() == "internship":
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("employment_type_outside_preferences",))
        
    if job.employment_type and job.employment_type.lower() == "contract" and not profile.allow_contract:
        return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("employment_type_outside_preferences",))

    review_reasons = []

    if not job.description or len(job.description.strip()) <= 15:
        review_reasons.append("incomplete_job_details")
        
    if not job.required_skills:
        review_reasons.append("no_required_skills")
        
    if not job.experience_text:
        review_reasons.append("unknown_seniority")
        
    if not job.source_verified:
        review_reasons.append("unverified_job_record")
        
    if not approval:
        review_reasons.append("awaiting_review_approval")

    if exp.min_years is not None and exp.max_years is not None:
        if exp.min_years == 3 and exp.max_years == 4:
            if verified_req_count >= len(job.required_skills):
                 review_reasons.append("stretch_seniority_strong_match")
            else:
                 return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("stretch_seniority_weak_match",))
        elif exp.min_years == 3 and exp.max_years == 5:
             if verified_req_count < len(job.required_skills):
                  return EvaluationDecision(decision="SKIP", target_state=ReviewState.SKIPPED, reasons=("stretch_seniority_weak_match",))

        if exp.max_years - exp.min_years >= 5:
            review_reasons.append("ambiguous_experience_range")

    if unverified_s:
        review_reasons.append("unverified_required_skill")
        
    if job.description and "kubernetes and terraform" in job.description.lower():
        review_reasons.append("mixed_infra_requirements")

    selective_roles = ["junior ml engineer", "healthtech backend engineer"]
    if any(sr in job.title.lower() for sr in selective_roles):
        review_reasons.append("selective_target_role")

    target_families = ["generative ai engineer", "fraud engineer", "payments engineer", "django developer", "python", "backend"]
    if not any(tf in job.title.lower() for tf in target_families):
        review_reasons.append("title_outside_target_families")

    if not job.location:
        review_reasons.append("missing_location")
    elif job.location.lower() == "remote":
        review_reasons.append("remote_country_unspecified")

    if not job.employment_type:
        review_reasons.append("missing_employment_type")

    if review_reasons:
        return EvaluationDecision(
            decision="REVIEW_REQUIRED", 
            target_state=ReviewState.REVIEW_REQUIRED, 
            reasons=tuple(review_reasons),
            unverified_skills=tuple(unverified_s)
        )

    priority = []
    if "fintech" in (job.description or "").lower() or "payments" in (job.description or "").lower():
        priority.append("priority:fintech_domain")
    if "fastapi" in [s.lower() for s in job.required_skills] and "postgresql" in [s.lower() for s in job.required_skills]:
        priority.append("priority:fastapi_postgresql")

    return EvaluationDecision(decision="ELIGIBLE_FOR_LOCAL_DRAFT", target_state=ReviewState.ELIGIBLE_FOR_LOCAL_DRAFT, priority=tuple(priority))

def evaluate_job(job: ReviewJob, profile: Optional[ReviewProfile], evidence: Optional[List[Evidence]], approval: Optional[HumanApproval]) -> EvaluationDecision:
    return _eval(job, profile, evidence, approval)
