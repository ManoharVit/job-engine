import enum
from datetime import datetime, UTC
import json
from src.models.audit import AuditLog
from src.models.job import JobModel

class JobState(str, enum.Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DRAFTED = "DRAFTED"
    APPLIED = "APPLIED"
    WITHDRAWN = "WITHDRAWN"

class InvalidStateTransitionError(Exception):
    pass

class ApprovalStateMachine:
    """
    State machine for job application lifecycle.
    
    Terminal states and their meanings:
    - REJECTED: The application was rejected by the company after application, or the user rejected the job before applying. Allowed reasons: "Not a fit", "Salary too low", "Company rejected", "Position filled".
    - WITHDRAWN: The user voluntarily withdrew their application or stopped the process. Allowed reasons: "Accepted another offer", "Lost interest", "No longer available".
    """
    VALID_TRANSITIONS = {
        JobState.NEW: {JobState.REVIEWED, JobState.REJECTED, JobState.WITHDRAWN},
        JobState.REVIEWED: {JobState.DRAFTED, JobState.REJECTED, JobState.WITHDRAWN},
        JobState.DRAFTED: {JobState.APPROVED, JobState.REJECTED, JobState.WITHDRAWN},
        JobState.APPROVED: {JobState.APPLIED, JobState.REJECTED, JobState.WITHDRAWN},
        JobState.APPLIED: {JobState.REJECTED, JobState.WITHDRAWN},
        JobState.REJECTED: set(),
        JobState.WITHDRAWN: set(),
    }

    def __init__(self, session):
        self.session = session

    def transition(self, job: JobModel, to_state: JobState, reason: str = "", dry_run: bool = False) -> tuple[JobModel, AuditLog]:
        """
        Transition job state.
        If dry_run is False, state transitions are persistent and committed to the database.
        If dry_run is True, it is a preview-only operation and no database mutations are committed.
        """
        current_state = JobState(job.application_status)
        
        if to_state not in self.VALID_TRANSITIONS.get(current_state, set()):
            raise InvalidStateTransitionError(f"Cannot transition from {current_state} to {to_state}")
            
        # APPROVED cannot transition to APPLIED before a DRAFTED state.
        # Ensure that if we are moving to APPLIED, there is a Draft in the database.
        if to_state == JobState.APPLIED:
            from src.models.job import Draft
            draft_exists = self.session.query(Draft).filter_by(job_id=job.id).first()
            if not draft_exists:
                raise InvalidStateTransitionError("Cannot transition to APPLIED: No Draft exists for this job.")
        
        job.application_status = to_state.value
        
        # Create audit log
        audit_log = AuditLog(
            action="STATE_TRANSITION",
            entity="JobModel",
            entity_id=str(job.id) if job.id else getattr(job, "source_job_id", ""),
            details=json.dumps({
                "from_state": current_state.value,
                "to_state": to_state.value,
                "reason": reason,
                "dry_run": dry_run
            })
        )
        if not dry_run:
            self.session.add(audit_log)
            self.session.commit()
        return job, audit_log
