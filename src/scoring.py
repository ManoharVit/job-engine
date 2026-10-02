import re
from typing import Tuple, Dict, Any
from src.models.job import JobCreate
from src.models.profile import CandidateProfile

def calculate_fit_score(job: JobCreate, profile: CandidateProfile) -> Tuple[float, Dict[str, float]]:
    """
    Calculate an explainable fit score (0.0 to 1.0) based on:
    - Role/title 25%
    - Core technical skills 30%
    - Domain 15%
    - Seniority 10%
    - Location/remote 10%
    - Evidence 10%
    """
    score = 0.0
    breakdown = {}
    
    # 1. Role/title (25%)
    role_score = 0.0
    title_lower = job.title.lower()
    if profile.target_roles:
        for role in profile.target_roles:
            if role.lower() in title_lower:
                role_score = 0.25
                break
    else:
        role_score = 0.25
    score += role_score
    breakdown['role_title'] = role_score
    
    # 2. Core technical skills (30%)
    skills_score = 0.0
    matched_skills = []
    job_skills_norm = [s.strip().lower() for s in job.required_skills]
    desc_lower = (job.description or "").lower()
    
    for skill in profile.must_have_skills:
        skill_lower = skill.strip().lower()
        
        # Handle negated skill mentions
        negated_pattern = r'(?:no|without|zero)\s+' + re.escape(skill_lower) + r'(?:$|\W)'
        is_negated = bool(re.search(negated_pattern, desc_lower))
        
        in_skills = skill_lower in job_skills_norm
        in_desc = bool(re.search(r'(?:^|\W)' + re.escape(skill_lower) + r'(?:$|\W)', desc_lower))
        
        if (in_skills or in_desc) and not is_negated:
            matched_skills.append(skill)
            
    if profile.must_have_skills:
        skills_score = 0.30 * (len(matched_skills) / len(profile.must_have_skills))
    else:
        skills_score = 0.30
    score += skills_score
    breakdown['core_technical_skills'] = skills_score
    
    # 3. Domain (15%)
    # Domain is hard to extract strictly from just title/company without LLM.
    # We will award 15% if the title matched, as a simple explainable proxy.
    domain_score = 0.15 if role_score > 0 else 0.0
    score += domain_score
    breakdown['domain'] = domain_score
    
    # 4. Seniority (10%)
    seniority_score = 0.0
    job_exp = (job.experience_level or "").lower()
    prof_exp = (profile.experience_range or "").lower()
    if job_exp and prof_exp:
        if job_exp in prof_exp or prof_exp in job_exp:
            seniority_score = 0.10
    else:
        # If job doesn't specify experience or profile doesn't, award half points
        seniority_score = 0.05
    score += seniority_score
    breakdown['seniority'] = seniority_score
    
    # 5. Location/remote (10%)
    location_score = 0.0
    job_loc = (job.location or "").lower()
    job_remote = (job.remote_status or "").lower()
    prof_remote = profile.remote_preference.lower() if profile.remote_preference else ""
    
    if not profile.preferred_locations and not prof_remote:
        location_score = 0.10
    elif job_remote and prof_remote and prof_remote in job_remote:
        location_score = 0.10
    elif job_remote == "remote" and prof_remote in ["remote", "hybrid"]:
        location_score = 0.10
    else:
        # Check location
        for loc in profile.preferred_locations:
            if loc.lower() in job_loc:
                location_score = 0.10
                break
    score += location_score
    breakdown['location_remote'] = location_score
    
    # 6. Evidence (10%)
    evidence_score = 0.0
    evidence_skills = [e.skill.strip().lower() for e in profile.evidence_registry if e.verified]
    evidence_hits = [s for s in matched_skills if s.strip().lower() in evidence_skills]
    
    if profile.must_have_skills:
        evidence_score = 0.10 * (len(evidence_hits) / len(profile.must_have_skills))
    else:
        evidence_score = 0.10
    score += evidence_score
    breakdown['evidence'] = evidence_score
    
    # Rounding to 3 decimal places
    return round(score, 3), {k: round(v, 3) for k, v in breakdown.items()}
