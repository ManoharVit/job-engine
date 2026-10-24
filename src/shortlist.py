"""Daily shortlist and prep-pack workflow (local only).

This module composes existing Job Engine building blocks:

* ``src.review.rubric.evaluate_job``      -> eligibility / exclusion decision
* ``src.scoring.calculate_fit_score``     -> explainable ranking score
* ``src.document_generation``             -> dry-run fit summary, cover letter, keywords
* ``src.screening``                       -> evidence-grounded screening-answer drafts

Safety contract:

* No network access, no browser automation, no external services.
* No database writes. ``build_shortlist`` only reads jobs, review records and
  audit rows.
* Nothing here can mark a job as applied or submitted. Submission status is
  read from records the user created explicitly.
* Every generated text is a draft for the user to review before use.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from src.canonicalize import canonicalize_url
from src.document_generation import DocumentGenerator, MissingEvidenceError
from src.models.job import JobRead
from src.models.profile import CandidateProfile, Evidence
from src.review.logs import job_ref_hash
from src.review.rubric import ALIAS_MAP, ReviewJob, ReviewProfile, evaluate_job
from src.scoring import calculate_fit_score
from src.screening import ScreeningQuestion, evaluate_screening_question

DRAFT_NOTICE = (
    "DRAFT - generated locally from your profile and verified evidence. "
    "Review and edit before using it anywhere."
)
MANUAL_APPLY_NOTICE = (
    "Job Engine never opens, fills, or submits applications. Open the job page "
    "in your normal browser and apply yourself."
)
JOB_RECORD_NOTICE = (
    "Job details are exactly as you imported them. Job Engine has not fetched "
    "or verified the listing."
)

MANUAL_SUBMISSION_ACTION = "MANUAL_SUBMISSION_RECORDED"
DEFAULT_FOLLOW_UP_DAYS = (7, 14)

SUBMISSION_STATES = ("SUBMISSION_UNCONFIRMED", "CONFIRMED_BY_HUMAN_REVIEW")
NOT_ATTEMPTED = "EXTERNAL_APPLICATION_NOT_ATTEMPTED"

# Rubric reasons that apply to every user-imported job before the user has
# looked at it. They are shown once as general notes, not as per-job flags.
BASELINE_REVIEW_REASONS = frozenset({"awaiting_review_approval", "unverified_job_record"})

Bucket = Literal["SHORTLISTED", "NEEDS_REVIEW", "EXCLUDED"]

REASON_LABELS: Dict[str, str] = {
    "missing_candidate_profile": "No candidate profile loaded, so fit cannot be checked.",
    "missing_candidate_evidence": "No verified evidence in your profile, so fit cannot be checked.",
    "missing_title": "Job record has no title.",
    "missing_company": "Job record has no company.",
    "title_seniority_excluded": "Title indicates seniority beyond your experience",
    "experience_min_years_ge_5": "Requires 5+ years of experience.",
    "excluded_skill_conflict": "Requires a skill you marked as excluded.",
    "missing_required_skills": "Required skills not in your profile",
    "primary_stack_excluded": "Primary stack is outside your target stack",
    "weak_fit_title": "Analyst/BI role, outside your target roles.",
    "research_role": "Research-scientist role, outside your target roles.",
    "research_credentials_required": "Mentions a PhD requirement.",
    "location_outside_preferences": "Location is outside your preferred locations.",
    "employment_type_outside_preferences": "Employment type is outside your preferences.",
    "stretch_seniority_weak_match": "Asks for 3+ years and not every required skill is verified.",
    "stretch_seniority_strong_match": "Asks for 3-4 years (a stretch), but every required skill is verified.",
    "incomplete_job_details": "Job description is missing or very short.",
    "no_required_skills": "No required skills listed, so skill fit cannot be checked.",
    "unknown_seniority": "Experience requirement is not stated.",
    "ambiguous_experience_range": "Experience range is very wide.",
    "unverified_required_skill": "Some required skills are in your profile but have no verified evidence",
    "mixed_infra_requirements": "Mixes heavy infrastructure requirements (Kubernetes and Terraform).",
    "selective_target_role": "Selective target role; check fit carefully.",
    "title_outside_target_families": "Title is outside your main target families (Python/backend/GenAI/fintech).",
    "missing_location": "Location is not stated.",
    "remote_country_unspecified": "Remote, but the country is not stated.",
    "missing_employment_type": "Employment type is not stated.",
    "excluded_company": "Company is in your excluded-companies list.",
    "excluded_role": "Title matches your excluded-roles list",
    "invalid_job_record": "Job record failed validation (for example, an invalid URL).",
    "awaiting_review_approval": "You have not reviewed this job yet.",
    "unverified_job_record": JOB_RECORD_NOTICE,
}

PRIORITY_LABELS: Dict[str, str] = {
    "priority:fintech_domain": "Fintech/payments domain matches your background.",
    "priority:fastapi_postgresql": "FastAPI + PostgreSQL stack matches your core skills.",
}


def _norm(skill: str) -> str:
    s = (skill or "").strip().lower()
    return ALIAS_MAP.get(s, s)


def describe_reason(code: str, detail: Optional[List[str]] = None) -> Dict[str, str]:
    """Return a ``{code, label}`` pair with a human-readable explanation."""
    base, _, suffix = code.partition(":")
    label = REASON_LABELS.get(base, base.replace("_", " "))
    extras = []
    if suffix:
        extras.append(suffix)
    if detail:
        extras.append(", ".join(detail))
    if extras:
        label = f"{label.rstrip('.')}: {'; '.join(extras)}."
    return {"code": code, "label": label}


# ---------------------------------------------------------------------------
# Batch link import parsing
# ---------------------------------------------------------------------------

LINK_LINE_FORMAT = "URL | Title | Company | Location | Experience | Required skills (comma-separated)"


def derive_source_job_id(canonical_url: str) -> str:
    """Stable identifier for a job that was imported without one."""
    return "url:" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]


def parse_link_lines(text: str) -> Tuple[List[dict], List[dict]]:
    """Parse pasted job links into job dicts for ``URLIngestor``.

    Each non-empty line uses ``LINK_LINE_FORMAT``. Only URL, title and company
    are mandatory. Job Engine never opens the URL, so a bare link without a
    title and company is rejected with an explanation instead of guessing.
    """
    items: List[dict] = []
    rejected: List[dict] = []
    for line_no, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            rejected.append({"line": line_no, "error": "Line must start with an http(s) job URL."})
            continue
        title = parts[1] if len(parts) > 1 else ""
        company = parts[2] if len(parts) > 2 else ""
        if not title or not company:
            rejected.append({
                "line": line_no,
                "error": (
                    "Add at least a title and company after the URL "
                    f"({LINK_LINE_FORMAT}). Job Engine does not open or fetch job pages."
                ),
            })
            continue
        skills = [s.strip() for s in (parts[5] if len(parts) > 5 else "").split(",") if s.strip()]
        canonical = canonicalize_url(url)
        items.append({
            "source": "manual_link",
            "source_job_id": derive_source_job_id(canonical),
            "url": url,
            "title": title,
            "company": company,
            "location": (parts[3] if len(parts) > 3 else "") or None,
            "experience_level": (parts[4] if len(parts) > 4 else "") or None,
            "required_skills": skills,
        })
    return items, rejected


# ---------------------------------------------------------------------------
# Shortlist models
# ---------------------------------------------------------------------------

class ShortlistRequest(BaseModel):
    profile: CandidateProfile
    evidence: List[Evidence]
    profile_version: str = "unversioned"
    allow_contract: bool = False
    job_ids: Optional[List[int]] = None


class ScreeningDraft(BaseModel):
    question: str
    classification: str
    suggested_answer: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    reason: str
    external_action: Literal["not_performed"] = "not_performed"


class FitSummary(BaseModel):
    headline: str
    reasons: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    matched: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    uncertain: List[str] = Field(default_factory=list)
    negated: List[str] = Field(default_factory=list)


class PrepPack(BaseModel):
    notice: str = DRAFT_NOTICE
    fit_summary: FitSummary
    cover_letter_draft: Optional[str] = None
    cover_letter_unavailable_reason: Optional[str] = None
    resume_keywords: List[str] = Field(default_factory=list)
    screening_drafts: List[ScreeningDraft] = Field(default_factory=list)
    screening_answer_manually: List[ScreeningDraft] = Field(default_factory=list)


class FollowUp(BaseModel):
    due_date: str
    overdue: bool


class SubmissionInfo(BaseModel):
    status: str = NOT_ATTEMPTED
    recorded_at: Optional[str] = None
    follow_ups: List[FollowUp] = Field(default_factory=list)
    note: str = "Not recorded as submitted. Job Engine never submits applications."


class ShortlistEntry(BaseModel):
    job_id: int
    title: str
    company: str
    location: Optional[str] = None
    employment_type: Optional[str] = None
    experience_level: Optional[str] = None
    source: str
    apply_url: Optional[str] = None
    job_ref_hash: Optional[str] = None
    bucket: Bucket
    rank: Optional[int] = None
    fit_score: float = 0.0
    fit_score_breakdown: Dict[str, float] = Field(default_factory=dict)
    rubric_decision: str
    eligibility_reasons: List[Dict[str, str]] = Field(default_factory=list)
    priority: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    unverified_skills: List[str] = Field(default_factory=list)
    submission: SubmissionInfo = Field(default_factory=SubmissionInfo)
    prep_pack: Optional[PrepPack] = None
    external_action: Literal["none"] = "none"


class ShortlistResponse(BaseModel):
    generated_at: str
    profile_version: str
    notices: List[str]
    counts: Dict[str, int]
    entries: List[ShortlistEntry]
    external_action: Literal["none"] = "none"


# ---------------------------------------------------------------------------
# Pure evaluation
# ---------------------------------------------------------------------------

def _safe_apply_url(url: str) -> Optional[str]:
    parsed = urlparse(url or "")
    if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
        return url
    return None


def _verified_refs(evidence: List[Evidence]) -> Dict[str, List[str]]:
    refs: Dict[str, List[str]] = {}
    for e in evidence:
        if e.verified:
            refs.setdefault(_norm(e.skill), []).append(e.reference)
    return refs


def _screening_drafts(job: JobRead, profile: CandidateProfile, evidence: List[Evidence]) -> Tuple[List[ScreeningDraft], List[ScreeningDraft]]:
    """Draft answers to common skill questions, grounded only in evidence for that exact skill."""
    safe: List[ScreeningDraft] = []
    manual: List[ScreeningDraft] = []
    for idx, skill in enumerate(sorted(job.required_skills or [], key=str.lower)):
        # Only evidence for this exact skill is considered, so substring
        # collisions (e.g. "SQL" inside "PostgreSQL") cannot leak an answer.
        skill_evidence = [e for e in evidence if _norm(e.skill) == _norm(skill)]
        questions = [
            ScreeningQuestion(question_id=f"skill-{idx}-yes-no", question_type="yes_no",
                              question_text=f"Do you have experience with {skill}?"),
            ScreeningQuestion(question_id=f"skill-{idx}-years", question_type="numeric",
                              question_text=f"How many years of experience do you have in {skill}?"),
        ]
        for q in questions:
            review = evaluate_screening_question(q, profile, skill_evidence)
            draft = ScreeningDraft(
                question=q.question_text,
                classification=review.classification,
                suggested_answer=review.suggested_answer,
                evidence_references=[r for e in skill_evidence if e.verified for r in [e.reference]]
                if review.classification == "SAFE_TO_SUGGEST" else [],
                reason=review.reason,
            )
            if review.classification == "SAFE_TO_SUGGEST":
                if q.question_type == "numeric":
                    draft.reason = (
                        "Years value comes from the structured duration recorded in your "
                        "verified evidence. Confirm it is accurate before using it."
                    )
                safe.append(draft)
            else:
                if draft.suggested_answer and draft.suggested_answer.startswith("[VERIFY"):
                    draft.suggested_answer = None
                manual.append(draft)
    return safe, manual


def _fit_summary(job: JobRead, profile: CandidateProfile, evidence: List[Evidence],
                 score: float, priority: List[str], flagged: List[Dict[str, str]]) -> FitSummary:
    generator = DocumentGenerator(None, dry_run=True)
    summary, _ = generator.generate_fit_summary(job, profile, evidence)
    refs = _verified_refs(evidence)
    required = job.required_skills or []
    verified_required = [s for s in required if _norm(s) in refs]
    if required:
        headline = f"{len(verified_required)}/{len(required)} required skills backed by verified evidence"
    else:
        headline = "No required skills listed; fit is based on title and description only"
    headline += f" | fit score {round(score * 100)}%"

    reasons = [f"{s}: verified ({'; '.join(refs[_norm(s)])})" for s in sorted(verified_required, key=str.lower)]
    reasons += [PRIORITY_LABELS.get(p, p) for p in priority]
    gaps = []
    if summary["missing"]:
        gaps.append("Not in your verified profile: " + ", ".join(summary["missing"]))
    if summary["uncertain"]:
        gaps.append("In your profile but without verified evidence: " + ", ".join(summary["uncertain"]))
    gaps += [f["label"] for f in flagged]
    return FitSummary(headline=headline, reasons=reasons, gaps=gaps, **summary)


def evaluate_entry(job: JobRead, profile: CandidateProfile, evidence: List[Evidence],
                   profile_version: str = "unversioned", allow_contract: bool = False) -> ShortlistEntry:
    """Evaluate one job. Pure: no I/O, no persistence."""
    review_profile = ReviewProfile(
        profile_version=profile_version,
        must_have_skills=profile.must_have_skills,
        nice_to_have_skills=profile.nice_to_have_skills,
        excluded_skills=profile.excluded_skills,
        allow_contract=allow_contract,
    )
    canonical = str(job.canonical_url)
    review_job = ReviewJob(
        source=job.source, canonical_url=canonical, title=job.title or "",
        company=job.company or "", location=job.location, employment_type=job.employment_type,
        description=job.description, required_skills=job.required_skills or [],
        experience_text=job.experience_level,
        # Imported records are user-provided, never verified by Job Engine.
        source_verified=False,
    )
    decision = evaluate_job(review_job, review_profile, evidence, approval=None)

    scored_profile = profile.model_copy(update={"evidence_registry": list(evidence)})
    score, breakdown = calculate_fit_score(job, scored_profile)

    entry = ShortlistEntry(
        job_id=job.id, title=job.title, company=job.company, location=job.location,
        employment_type=job.employment_type, experience_level=job.experience_level,
        source=job.source, apply_url=_safe_apply_url(canonical), job_ref_hash=job_ref_hash(canonical),
        bucket="EXCLUDED", fit_score=score, fit_score_breakdown=breakdown,
        rubric_decision=decision.decision, priority=list(decision.priority),
        missing_skills=list(decision.missing_skills), unverified_skills=list(decision.unverified_skills),
    )

    # Profile-level exclusions the rubric does not cover.
    company_l = (job.company or "").strip().lower()
    title_l = (job.title or "").lower()
    if any(c.strip().lower() == company_l for c in profile.excluded_companies if c.strip()):
        entry.rubric_decision = "SKIP"
        entry.eligibility_reasons = [describe_reason("excluded_company")]
        return entry
    hit_roles = [r for r in profile.excluded_roles if r.strip() and r.strip().lower() in title_l]
    if hit_roles:
        entry.rubric_decision = "SKIP"
        entry.eligibility_reasons = [describe_reason("excluded_role", hit_roles)]
        return entry

    if decision.decision == "SKIP":
        entry.eligibility_reasons = [
            describe_reason(r, list(decision.missing_skills) if r == "missing_required_skills" else None)
            for r in decision.reasons
        ]
        return entry

    flagged = [
        describe_reason(r, list(decision.unverified_skills) if r == "unverified_required_skill" else None)
        for r in decision.reasons if r not in BASELINE_REVIEW_REASONS
    ]
    entry.bucket = "NEEDS_REVIEW" if flagged else "SHORTLISTED"
    entry.eligibility_reasons = flagged

    generator = DocumentGenerator(None, dry_run=True)
    keywords, _ = generator.generate_resume_keyword_suggestions(job, evidence)
    cover_letter, unavailable = None, None
    try:
        cover_letter, _ = generator.generate_cover_letter_draft(job, profile, evidence)
    except MissingEvidenceError as exc:
        unavailable = str(exc)
    safe, manual = _screening_drafts(job, profile, evidence)

    entry.prep_pack = PrepPack(
        fit_summary=_fit_summary(job, profile, evidence, score, entry.priority, flagged),
        cover_letter_draft=cover_letter,
        cover_letter_unavailable_reason=unavailable,
        resume_keywords=keywords,
        screening_drafts=safe,
        screening_answer_manually=manual,
    )
    return entry


def rank_entries(entries: List[ShortlistEntry]) -> List[ShortlistEntry]:
    """Order shortlisted jobs first, then flagged, then excluded; assign ranks."""
    order = {"SHORTLISTED": 0, "NEEDS_REVIEW": 1, "EXCLUDED": 2}
    ranked = sorted(entries, key=lambda e: (order[e.bucket], -e.fit_score, -len(e.priority), e.title.lower(), e.job_id))
    rank = 0
    for e in ranked:
        if e.bucket != "EXCLUDED":
            rank += 1
            e.rank = rank
        else:
            e.rank = None
    return ranked


def follow_up_schedule(recorded_at: datetime, days: List[int], now: datetime) -> List[FollowUp]:
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    return [
        FollowUp(due_date=(recorded_at + timedelta(days=d)).date().isoformat(),
                 overdue=(recorded_at + timedelta(days=d)) < now)
        for d in sorted(set(days))
    ]


# ---------------------------------------------------------------------------
# Read-only database composition
# ---------------------------------------------------------------------------

def _submission_info(db, job_id: int, now: datetime) -> SubmissionInfo:
    from src.models.audit import AuditLog
    from src.review.models import ReviewRecord

    rr = db.query(ReviewRecord).filter_by(job_id=job_id).first()
    state = rr.state if rr else None
    if state not in SUBMISSION_STATES:
        return SubmissionInfo()

    info = SubmissionInfo(status=state)
    audit = (
        db.query(AuditLog)
        .filter_by(action=MANUAL_SUBMISSION_ACTION, entity_id=str(job_id))
        .order_by(AuditLog.id.desc())
        .first()
    )
    if audit is not None:
        details = json.loads(audit.details or "{}")
        days = details.get("follow_up_days") or list(DEFAULT_FOLLOW_UP_DAYS)
        info.recorded_at = audit.timestamp.isoformat() if audit.timestamp else None
        if audit.timestamp:
            info.follow_ups = follow_up_schedule(audit.timestamp, days, now)
        info.note = "You recorded that you submitted this application yourself."
    else:
        info.note = "Marked unconfirmed by an earlier review or migration; no self-recorded submission."
    if state == "CONFIRMED_BY_HUMAN_REVIEW":
        info.note += " Confirmation evidence was recorded by you."
    else:
        info.note += " Add confirmation evidence (status page, email, or confirmation text) when you have it."
    return info


def build_shortlist(db, request: ShortlistRequest, now: Optional[datetime] = None) -> ShortlistResponse:
    """Build the ranked shortlist. Reads only; never adds, updates or commits."""
    from src.models.job import JobModel

    now = now or datetime.now(timezone.utc)
    query = db.query(JobModel)
    if request.job_ids:
        query = query.filter(JobModel.id.in_(request.job_ids))

    entries: List[ShortlistEntry] = []
    for job_row in query.order_by(JobModel.id).all():
        try:
            job = JobRead.model_validate(job_row)
        except ValidationError:
            entries.append(ShortlistEntry(
                job_id=job_row.id, title=job_row.title or "", company=job_row.company or "",
                source=job_row.source or "", bucket="EXCLUDED", rubric_decision="SKIP",
                eligibility_reasons=[describe_reason("invalid_job_record")],
            ))
            continue
        entry = evaluate_entry(job, request.profile, request.evidence,
                               request.profile_version, request.allow_contract)
        entry.submission = _submission_info(db, job.id, now)
        entries.append(entry)

    ranked = rank_entries(entries)
    counts = {b: sum(1 for e in ranked if e.bucket == b) for b in ("SHORTLISTED", "NEEDS_REVIEW", "EXCLUDED")}
    counts["total"] = len(ranked)
    return ShortlistResponse(
        generated_at=now.isoformat(),
        profile_version=request.profile_version,
        notices=[MANUAL_APPLY_NOTICE, JOB_RECORD_NOTICE, DRAFT_NOTICE],
        counts=counts,
        entries=ranked,
    )
