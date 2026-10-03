from typing import List, Dict, Any
from pydantic import BaseModel, Field
from src.models.job import JobRead
from src.models.profile import CandidateProfile, Evidence
from src.eligibility import normalize_skill

class InterviewEligibility(BaseModel):
    decision: str
    reason: str | None

class MatchedSkill(BaseModel):
    skill: str
    evidence_reference: str

class PracticeQuestion(BaseModel):
    skill: str
    question: str

class InterviewGuide(BaseModel):
    eligibility: InterviewEligibility
    matched_skills: List[MatchedSkill] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    uncertain_requirements: List[str] = Field(default_factory=list)
    excluded_requirements: List[str] = Field(default_factory=list)
    unverified_requirements: List[str] = Field(default_factory=list)
    technical_topics: List[str] = Field(default_factory=list)
    practice_questions: List[PracticeQuestion] = Field(default_factory=list)
    evidence_talking_points: List[str] = Field(default_factory=list)
    gap_handling_language: List[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        md = [
            f"## Interview Preparation Guide",
            f"**Decision:** {self.eligibility.decision}",
            f"**Reason:** {self.eligibility.reason or 'N/A'}",
            "",
            "## Matched Skills"
        ]
        for ms in self.matched_skills:
            md.append(f"- **{ms.skill}**: {ms.evidence_reference}")

        md.append("")
        md.append("## Missing Requirements")
        for req in self.missing_requirements:
            md.append(f"- {req}")

        md.append("")
        md.append("## Uncertain Requirements")
        for req in self.uncertain_requirements:
            md.append(f"- {req}")

        md.append("")
        md.append("## Excluded Requirements")
        for req in self.excluded_requirements:
            md.append(f"- {req}")

        md.append("")
        md.append("## Unverified Requirements")
        for req in self.unverified_requirements:
            md.append(f"- {req}")

        md.append("")
        md.append("## Technical Topics")
        for t in self.technical_topics:
            md.append(f"- {t}")

        md.append("")
        md.append("## Practice Questions")
        for q in self.practice_questions:
            md.append(f"- **{q.skill}**: {q.question}")

        md.append("")
        md.append("## Evidence-Grounded Talking Points")
        for t in self.evidence_talking_points:
            md.append(f"- {t}")

        md.append("")
        md.append("## Gap-Handling Language")
        for g in self.gap_handling_language:
            md.append(f"- {g}")

        return "\n".join(md)


def generate_interview_guide(
    job: JobRead,
    profile: CandidateProfile,
    evidence: List[Evidence],
    eligibility: Dict[str, Any]
) -> InterviewGuide:
    
    # Extract norm lists
    norm_must_have = {normalize_skill(s): s for s in profile.must_have_skills}
    norm_nice_have = {normalize_skill(s): s for s in profile.nice_to_have_skills}
    norm_excluded = {normalize_skill(s): s for s in profile.excluded_skills}

    norm_evidence = {}
    for ev in evidence:
        n_skill = normalize_skill(ev.skill)
        if n_skill not in norm_evidence:
            norm_evidence[n_skill] = []
        norm_evidence[n_skill].append(ev)

    matched_skills = []
    missing_requirements = []
    uncertain_requirements = []
    excluded_requirements = []
    unverified_requirements = []
    
    technical_topics = []
    practice_questions = []
    evidence_talking_points = []
    gap_handling_language = []

    for req in job.required_skills:
        n_req = normalize_skill(req)
        
        # Determine topic and practice questions based strictly on the required skill
        technical_topics.append(f"Advanced concepts and practical application of {req}.")
        practice_questions.append(PracticeQuestion(
            skill=req,
            question=f"Describe a challenging problem you solved using {req}."
        ))

        # Check exclusion
        if n_req in norm_excluded:
            excluded_requirements.append(req)
            missing_requirements.append(req)
            gap_handling_language.append(f"I deliberately avoid roles heavily dependent on {req}, but I am open to discussing it.")
            continue
            
        # Check if claimed in profile
        is_claimed = n_req in norm_must_have or n_req in norm_nice_have
        
        # Check evidence
        evs = norm_evidence.get(n_req, [])
        verified_evs = [ev for ev in evs if ev.verified]
        unverified_evs = [ev for ev in evs if not ev.verified]
        
        if verified_evs:
            for vev in verified_evs:
                matched_skills.append(MatchedSkill(skill=req, evidence_reference=vev.reference))
                evidence_talking_points.append(f"Discuss your work on {vev.reference} demonstrating {req}.")
        elif unverified_evs:
            # We have evidence, but unverified
            unverified_requirements.append(f"[VERIFY: Have you used {req}? Please provide verified evidence. Found unverified evidence: {unverified_evs[0].reference}]")
            missing_requirements.append(req)
            gap_handling_language.append(f"I have some exposure to {req} but need to verify my experience.")
        elif is_claimed:
            # Claimed but no evidence
            unverified_requirements.append(f"[VERIFY: Have you used {req}? Please provide evidence.]")
            missing_requirements.append(req)
            gap_handling_language.append(f"I have some exposure to {req} but need to verify my experience.")
        else:
            # Not claimed, no evidence
            uncertain_requirements.append(req)
            missing_requirements.append(req)
            gap_handling_language.append(f"I do not have direct experience with {req}, but I am open to learning it.")

    if eligibility.get("reason") == "unknown_seniority":
        uncertain_requirements.append("Seniority level")

    guide = InterviewGuide(
        eligibility=InterviewEligibility(
            decision=eligibility.get("decision", "UNKNOWN"),
            reason=eligibility.get("reason")
        ),
        matched_skills=matched_skills,
        missing_requirements=missing_requirements,
        uncertain_requirements=uncertain_requirements,
        excluded_requirements=excluded_requirements,
        unverified_requirements=unverified_requirements,
        technical_topics=technical_topics,
        practice_questions=practice_questions,
        evidence_talking_points=evidence_talking_points,
        gap_handling_language=gap_handling_language
    )
    
    return guide
