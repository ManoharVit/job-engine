from typing import Literal, List, Optional
from pydantic import BaseModel, Field
import re
from src.models.profile import CandidateProfile, Evidence

class ScreeningQuestion(BaseModel):
    question_id: str
    question_text: str
    question_type: Literal[
        "yes_no",
        "multiple_choice",
        "free_text",
        "numeric",
        "date",
        "unknown"
    ]
    options: List[str] = Field(default_factory=list)
    required: bool = True

class ScreeningAnswerReview(BaseModel):
    question_id: str
    classification: Literal[
        "SAFE_TO_SUGGEST",
        "REQUIRES_HUMAN_ANSWER",
        "UNSUPPORTED_CLAIM",
        "SENSITIVE_OR_LEGAL",
        "AMBIGUOUS"
    ]
    suggested_answer: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    reason: str
    external_action: Literal["not_performed"] = "not_performed"

def evaluate_screening_question(question: ScreeningQuestion, profile: CandidateProfile, evidence: List[Evidence]) -> ScreeningAnswerReview:
    text = question.question_text.lower()
    
    # Sensitive or legal
    if any(k in text for k in ["visa", "citizenship", "work authorization", "disability", "criminal", "gender", "religion", "caste", "marital status", "medical", "age", "auth"]):
        return ScreeningAnswerReview(
            question_id=question.question_id,
            classification="SENSITIVE_OR_LEGAL",
            reason="Sensitive or legal question.",
            external_action="not_performed"
        )
        
    # Requires human answer
    if any(k in text for k in ["in-person interview", "shift", "salary", "relocate", "notice period", "availability"]):
        return ScreeningAnswerReview(
            question_id=question.question_id,
            classification="REQUIRES_HUMAN_ANSWER",
            reason="This is a personal availability and preference question. The candidate must answer manually.",
            external_action="not_performed"
        )
        
    # Technology / Years of experience
    if "experience" in text or "years" in text or "azure databricks" in text or "pyspark" in text or "python" in text:
        if "how many years" in text or "years of experience" in text:
            # Need structured duration
            found = []
            for e in evidence:
                if e.verified and e.skill and e.skill.lower() in text:
                    if e.structured_duration is not None and (e.reviewer or e.reference):
                        found.append(e)
            
            if found:
                return ScreeningAnswerReview(
                    question_id=question.question_id,
                    classification="SAFE_TO_SUGGEST",
                    suggested_answer=str(found[0].structured_duration),
                    evidence_references=[e.skill for e in found],
                    reason="Supported by verified evidence.",
                    external_action="not_performed"
                )
            else:
                return ScreeningAnswerReview(
                    question_id=question.question_id,
                    classification="UNSUPPORTED_CLAIM",
                    suggested_answer=None,
                    reason="Answer manually only if you know the accurate value",
                    external_action="not_performed"
                )
        
        # Boolean experience
        found = []
        for e in evidence:
            if e.verified and e.skill and e.skill.lower() in text:
                found.append(e)
                
        if found:
            return ScreeningAnswerReview(
                question_id=question.question_id,
                classification="SAFE_TO_SUGGEST",
                suggested_answer="Yes",
                evidence_references=[e.skill for e in found],
                reason="Supported by verified evidence.",
                external_action="not_performed"
            )
        else:
            return ScreeningAnswerReview(
                question_id=question.question_id,
                classification="UNSUPPORTED_CLAIM",
                suggested_answer=f"[VERIFY: {question.question_text}]",
                reason="No verified evidence supports this claim.",
                external_action="not_performed"
            )
            
    # Fallback
    return ScreeningAnswerReview(
        question_id=question.question_id,
        classification="AMBIGUOUS",
        reason="Ambiguous question mapping.",
        external_action="not_performed"
    )
