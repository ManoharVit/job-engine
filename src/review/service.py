import json
import uuid
from datetime import datetime, timezone
from src.models.audit import AuditLog
from src.models.job import Draft, JobModel
from src.review.evidence import SubmissionEvidenceRecord
from src.review.logs import LocalLogRecord, job_ref_hash
from src.review.models import ReviewRecord, SubmissionEvidenceRow
from src.review.states import ReviewState as S, check_transition, Actor, ReviewTransitionError
from src.review.rubric import evaluate_job, ReviewJob, ReviewProfile, HumanApproval

def _sanitize(text: str) -> str:
    if not text:
        return text
    import re
    text = re.sub(r'https?://[^\s]+', '[URL REDACTED]', text)
    text = re.sub(r'applied' + r'\s+successfully', 'submitted', text, flags=re.IGNORECASE)
    return text

class ReviewService:
    def __init__(self, db_session):
        self.db = db_session

    def _get_rr(self, job_id: int) -> ReviewRecord:
        rr = self.db.query(ReviewRecord).filter_by(job_id=job_id).first()
        if not rr:
            rr = ReviewRecord(job_id=job_id, state=S.DISCOVERED.value)
            self.db.add(rr)
            self.db.flush()
        return rr

    def _get_job(self, job_id: int) -> JobModel:
        return self.db.query(JobModel).filter_by(id=job_id).one()

    def _log(self, job_id: int, log: LocalLogRecord):
        rr = self._get_rr(job_id)
        hist = json.loads(rr.history_json)
        hist.append(log.model_dump())
        rr.history_json = json.dumps(hist)
        rr.state = log.to_state
        self.db.add(rr)
        print(json.dumps(log.to_structured_event()))

    def state(self, job_id: int) -> S:
        return S(self._get_rr(job_id).state)

    def history(self, job_id: int) -> list[LocalLogRecord]:
        rr = self._get_rr(job_id)
        return [LocalLogRecord(**item) for item in json.loads(rr.history_json)]

    def register(self, job_id: int, source: str, source_verified: bool):
        job = self._get_job(job_id)
        job.source = source
        self._get_rr(job_id)
        self.db.commit()

    def normalize(self, job_id: int) -> LocalLogRecord:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.NORMALIZED
        
        if not job.title or not job.title.strip():
            dst = S.SKIPPED
            reasons = ["missing_title"]
        else:
            reasons = []

        check_transition(src, dst, Actor.SYSTEM)
        log = LocalLogRecord(
            actor="system", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE" if dst == S.NORMALIZED else "SKIP",
            reasons=reasons, submission_claim="none", message="Normalized"
        )
        self._log(job_id, log)
        self.db.commit()
        return log

    def record_review_approval(self, job_id: int, verified_by: str) -> HumanApproval:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        
        audit = AuditLog(
            action="REVIEW_APPROVAL",
            entity="ReviewRecord",
            entity_id=str(job_id),
            details=json.dumps({"verified_by": verified_by, "job_ref": job_ref_hash(job.canonical_url)})
        )
        self.db.add(audit)
        self.db.commit()
        
        return HumanApproval(
            verified_by=verified_by,
            approved_at=datetime.now(timezone.utc),
            review_audit_id=str(audit.id)
        )

    def evaluate(self, job_id: int, profile: ReviewProfile, evidence: list, approval: HumanApproval) -> LocalLogRecord:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        
        rj = ReviewJob(
            source=job.source, canonical_url=job.canonical_url, title=job.title or "",
            company=job.company or "", location=job.location, employment_type=job.employment_type,
            description=job.description, required_skills=job.required_skills or [],
            experience_text=job.experience_level, source_verified=True
        )
        
        if approval and approval.verified_by:
            audit = self.db.query(AuditLog).filter_by(id=int(approval.review_audit_id)).first()
            if not audit or audit.entity_id != str(job_id) or audit.action != "REVIEW_APPROVAL":
                raise ReviewTransitionError("Invalid approval")

        decision = evaluate_job(rj, profile, evidence, approval)
        dst = decision.target_state
        
        check_transition(src, dst, Actor.SYSTEM)
        
        log = LocalLogRecord(
            actor="system", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version=profile.profile_version if profile else "none",
            from_state=src.value, to_state=dst.value, decision=decision.decision,
            reasons=list(decision.reasons), submission_claim="none", message="Evaluated"
        )
        self._log(job_id, log)
        self.db.commit()
        return log

    def mark_drafted(self, job_id: int, verified_by: str):
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.DRAFTED
        if src != S.ELIGIBLE_FOR_LOCAL_DRAFT:
            raise ReviewTransitionError(f"Cannot draft from {src}")
        check_transition(src, dst, Actor.HUMAN)
        
        draft = self.db.query(Draft).filter_by(job_id=job_id).first()
        if not draft:
            raise ReviewTransitionError("Draft not found")

        log = LocalLogRecord(
            actor="human", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[], submission_claim="none", message="Drafted", verified_by=verified_by
        )
        self._log(job_id, log)
        self.db.commit()

    def mark_duplicate(self, job_id: int, duplicate_of_job_id: int):
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.DUPLICATE
        check_transition(src, dst, Actor.SYSTEM)
        
        log = LocalLogRecord(
            actor="system", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[], submission_claim="none", message="Duplicate"
        )
        self._log(job_id, log)
        rr.duplicate_of = duplicate_of_job_id
        self.db.commit()

    def human_decide(self, job_id: int, target_state: S, verified_by: str, reason: str) -> LocalLogRecord:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = target_state
        
        if not reason or not reason.strip():
            raise ReviewTransitionError("Reason required")
            
        if dst in (S.APPROVED_FOR_EXPORT, S.SUBMISSION_UNCONFIRMED, S.CONFIRMED_BY_HUMAN_REVIEW):
             raise ReviewTransitionError("Cannot target submission states")
             
        check_transition(src, dst, Actor.HUMAN)
        
        log = LocalLogRecord(
            actor="human", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[reason], submission_claim="none", message="Human decide", verified_by=verified_by
        )
        self._log(job_id, log)
        self.db.commit()
        return log

    def approve_export(self, job_id: int, verified_by: str, profile: ReviewProfile, evidence: list) -> LocalLogRecord:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.APPROVED_FOR_EXPORT
        
        if src != S.DRAFTED:
            raise ReviewTransitionError(f"Cannot approve export from {src}")
            
        if not verified_by or not verified_by.strip():
            raise ReviewTransitionError("Reviewer required")
            
        check_transition(src, dst, Actor.HUMAN)
        
        rj = ReviewJob(
            source=job.source, canonical_url=job.canonical_url, title=job.title or "",
            company=job.company or "", location=job.location, employment_type=job.employment_type,
            description=job.description, required_skills=job.required_skills or [],
            experience_text=job.experience_level, source_verified=True
        )
        
        verified = [e.skill.lower() for e in evidence if e.verified]
        for req in (job.required_skills or []):
            if req.lower() not in verified:
                raise ReviewTransitionError("verified evidence required for every required skill")
                
        mock_approval = HumanApproval(verified_by=verified_by, approved_at=datetime.now(timezone.utc), review_audit_id="dummy")
        decision = evaluate_job(rj, profile, evidence, mock_approval)
        if decision.decision == "SKIP":
            raise ReviewTransitionError("Rubric hard rule skipped")

        log = LocalLogRecord(
            actor="human", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version=profile.profile_version,
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[], submission_claim="none", message="Export approved", verified_by=verified_by
        )
        
        try:
            self._log(job_id, log)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
            
        return log

    def mark_exported(self, job_id: int) -> tuple[dict, LocalLogRecord]:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.EXTERNAL_APPLICATION_NOT_ATTEMPTED
        
        if src != S.APPROVED_FOR_EXPORT:
            raise ReviewTransitionError(f"Cannot mark exported from {src}")
            
        check_transition(src, dst, Actor.SYSTEM)
        
        draft = self.db.query(Draft).filter_by(job_id=job_id).first()
        pkg = {
            "external_action": "none",
            "submission_status": "EXTERNAL_APPLICATION_NOT_ATTEMPTED",
            "draft": draft.content if draft else ""
        }
        rr.export_package_json = json.dumps(pkg)
        
        log = LocalLogRecord(
            actor="system", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[], submission_claim="none", message="Marked exported"
        )
        self._log(job_id, log)
        self.db.commit()
        return pkg, log

    def report_unconfirmed_submission(self, job_id: int, verified_by: str, note: str) -> LocalLogRecord:
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.SUBMISSION_UNCONFIRMED
        check_transition(src, dst, Actor.HUMAN)
        
        log = LocalLogRecord(
            actor="human", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[note], submission_claim="unconfirmed", message="Unconfirmed submission reported", verified_by=verified_by
        )
        self._log(job_id, log)
        self.db.commit()
        return log



    def open_evidence_review(self, job_id: int, verified_by: str) -> str:
        audit = AuditLog(
            action="OPEN_EVIDENCE_REVIEW",
            entity="ReviewRecord",
            entity_id=str(job_id),
            details=json.dumps({"verified_by": verified_by})
        )
        self.db.add(audit)
        self.db.commit()
        return str(audit.id)

    def confirm_submission(self, job_id: int, evidence: SubmissionEvidenceRecord) -> LocalLogRecord:
        if evidence is None:
            raise ReviewTransitionError("evidence is mandatory")
        
        job = self._get_job(job_id)
        rr = self._get_rr(job_id)
        src = S(rr.state)
        dst = S.CONFIRMED_BY_HUMAN_REVIEW
        check_transition(src, dst, Actor.HUMAN)
        
        if evidence.job_ref_hash != job_ref_hash(job.canonical_url):
            raise ReviewTransitionError("job_ref_hash mismatch")
            
        audit = self.db.query(AuditLog).filter_by(id=int(evidence.review_audit_id)).first()
        if not audit or audit.entity_id != str(job_id) or audit.action != "OPEN_EVIDENCE_REVIEW":
            raise ReviewTransitionError("audit record mismatch")

        d = json.loads(audit.details or "{}")
        if d.get("verified_by") != evidence.verified_by:
             raise ReviewTransitionError("reviewer mismatch")

        row = SubmissionEvidenceRow(
            job_id=job_id, evidence_id=evidence.evidence_id,
            evidence_type=evidence.evidence_type, observed_at=evidence.observed_at,
            source_reference_hash=evidence.source_reference_hash, verified_by=evidence.verified_by,
            verified_at=evidence.verified_at, review_audit_id=evidence.review_audit_id
        )
        self.db.add(row)
        
        log = LocalLogRecord(
            actor="human", source=job.source, job_ref_hash=job_ref_hash(job.canonical_url),
            title=job.title or "", company=job.company or "", profile_version="none",
            from_state=src.value, to_state=dst.value, decision="STATE_CHANGE",
            reasons=[evidence.evidence_type], submission_claim="confirmed_by_human_review", 
            message="Confirmed by human review.", verified_by=evidence.verified_by,
            evidence_record_id=evidence.evidence_id
        )
        
        log_audit = AuditLog(
            action="SUBMISSION_CONFIRMED_BY_HUMAN",
            entity="ReviewRecord",
            entity_id=str(job_id),
            details=json.dumps({"msg": _sanitize("Confirmed by human review.")})
        )
        self.db.add(log_audit)
        
        try:
            self._log(job_id, log)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
            
        return log
