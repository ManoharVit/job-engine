import json
import hashlib
from typing import List, Dict, Any, Tuple
from sqlalchemy.exc import IntegrityError
from src.models.job import JobModel, Draft
from src.models.profile import CandidateProfile, Evidence
from src.models.audit import AuditLog

class MissingEvidenceError(Exception):
    pass

class DuplicateDraftError(Exception):
    pass

class DocumentGenerator:
    """
    Generates documents based on job and profile.
    
    Dry-run duplicate detection is process-local unless persistence is enabled.
    """
    def __init__(self, session, dry_run: bool = True):
        self.session = session
        self.dry_run = dry_run

    def _get_verified_skills(self, verified_evidences: List[Evidence]) -> set:
        return {e.skill.lower() for e in verified_evidences if e.verified}

    def _log_audit(self, action: str, entity_id: str, details: dict, commit: bool = True) -> AuditLog:
        audit_log = AuditLog(
            action=action,
            entity="DocumentGenerator",
            entity_id=entity_id,
            details=json.dumps(details)
        )
        if not self.dry_run:
            self.session.add(audit_log)
            if commit:
                self.session.commit()
        return audit_log

    def generate_fit_summary(self, job: JobModel, profile: CandidateProfile, verified_evidences: List[Evidence]) -> Tuple[Dict[str, List[str]], AuditLog]:
        """Fit-summary generator showing matched, missing, uncertain, and negated requirements."""
        verified_skills = self._get_verified_skills(verified_evidences)
        job_reqs = set(s.lower() for s in (job.required_skills or []))
        job_prefs = set(s.lower() for s in (job.preferred_skills or []))
        all_job_skills = job_reqs.union(job_prefs)

        matched = []
        missing = []
        uncertain = []
        negated = []

        profile_must_haves = set(s.lower() for s in profile.must_have_skills)
        profile_nice_to_haves = set(s.lower() for s in profile.nice_to_have_skills)
        profile_excluded = set(s.lower() for s in getattr(profile, "excluded_skills", []))

        for req in all_job_skills:
            if req in profile_excluded:
                negated.append(req)
            elif req in verified_skills:
                matched.append(req)
            elif req in profile_must_haves or req in profile_nice_to_haves:
                uncertain.append(req)
            else:
                missing.append(req)
        
        summary = {
            "matched": sorted(matched),
            "missing": sorted(missing),
            "uncertain": sorted(uncertain),
            "negated": sorted(negated)
        }
        
        audit_log = self._log_audit(
            action="GENERATE_FIT_SUMMARY",
            entity_id=str(job.id) if job.id else getattr(job, "source_job_id", ""),
            details=summary
        )
        return summary, audit_log

    def generate_resume_keyword_suggestions(self, job: JobModel, verified_evidences: List[Evidence]) -> Tuple[List[str], AuditLog]:
        """Resume-keyword suggestion generator."""
        verified_skills = self._get_verified_skills(verified_evidences)
        job_skills = set(s.lower() for s in (job.required_skills or []) + (job.preferred_skills or []))
        
        suggestions = sorted([skill for skill in job_skills if skill in verified_skills])
        
        audit_log = self._log_audit(
            action="GENERATE_RESUME_KEYWORDS",
            entity_id=str(job.id) if job.id else getattr(job, "source_job_id", ""),
            details={"suggestions": suggestions}
        )
        return suggestions, audit_log

    def generate_cover_letter_draft(self, job: JobModel, profile: CandidateProfile, verified_evidences: List[Evidence]) -> Tuple[str, AuditLog]:
        """Tailored cover-letter draft generator. Marks unsupported claims as [VERIFY]."""
        verified_skills = self._get_verified_skills(verified_evidences)
        job_id_str = str(job.id) if job.id else getattr(job, "source_job_id", "")
        
        if not self.dry_run:
            if not job.id:
                raise ValueError("Job must be saved to DB before generating drafts in real mode.")
            
            existing_draft = self.session.query(Draft).filter_by(
                job_id=job.id, document_type="cover_letter", version=1
            ).first()
            if existing_draft:
                raise DuplicateDraftError("Draft already generated for this job.")
        
        has_verified = any(e.verified for e in verified_evidences)
        if not has_verified:
            raise MissingEvidenceError("No verified evidence provided for cover letter generation.")
            
        draft_parts = []
        draft_parts.append(f"Dear Hiring Manager at {job.company},")
        draft_parts.append(f"I am writing to apply for the {job.title} position.")
        
        for skill in sorted(job.required_skills or []):
            if skill.lower() in verified_skills:
                draft_parts.append(f"I have proven experience with {skill}.")
            else:
                draft_parts.append(f"[VERIFY: {skill} experience]")
                
        draft = "\\n\\n".join(draft_parts)
        
        content_hash = hashlib.sha256(draft.encode('utf-8')).hexdigest()
        audit_log = self._log_audit(
            action="GENERATE_COVER_LETTER_DRAFT",
            entity_id=job_id_str,
            details={"content_hash": content_hash, "metadata": {"length": len(draft)}},
            commit=False
        )
        
        if not self.dry_run:
            try:
                new_draft = Draft(job_id=job.id, document_type="cover_letter", version=1, content=draft)
                self.session.add(new_draft)
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
                raise DuplicateDraftError("Draft already generated for this job concurrently.")
            except Exception:
                self.session.rollback()
                raise
                
        return draft, audit_log
