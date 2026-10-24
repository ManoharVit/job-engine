import enum

class ReviewState(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    NORMALIZED = "NORMALIZED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ELIGIBLE_FOR_LOCAL_DRAFT = "ELIGIBLE_FOR_LOCAL_DRAFT"
    DRAFTED = "DRAFTED"
    APPROVED_FOR_EXPORT = "APPROVED_FOR_EXPORT"
    EXTERNAL_APPLICATION_NOT_ATTEMPTED = "EXTERNAL_APPLICATION_NOT_ATTEMPTED"
    SUBMISSION_UNCONFIRMED = "SUBMISSION_UNCONFIRMED"
    CONFIRMED_BY_HUMAN_REVIEW = "CONFIRMED_BY_HUMAN_REVIEW"
    SKIPPED = "SKIPPED"
    DUPLICATE = "DUPLICATE"

class Actor(str, enum.Enum):
    SYSTEM = "system"
    HUMAN = "human"

class ReviewTransitionError(Exception):
    pass

SYSTEM_ALLOWED_TARGETS = {
    ReviewState.NORMALIZED,
    ReviewState.DUPLICATE,
    ReviewState.SKIPPED,
    ReviewState.REVIEW_REQUIRED,
    ReviewState.ELIGIBLE_FOR_LOCAL_DRAFT,
    ReviewState.EXTERNAL_APPLICATION_NOT_ATTEMPTED,
}

TERMINAL_STATES = {
    ReviewState.SKIPPED,
    ReviewState.DUPLICATE,
    ReviewState.CONFIRMED_BY_HUMAN_REVIEW,
}

def check_transition(src: ReviewState, dst: ReviewState, actor: Actor):
    if src in TERMINAL_STATES:
        raise ReviewTransitionError(f"Cannot transition from terminal state {src}")

    if dst == ReviewState.CONFIRMED_BY_HUMAN_REVIEW:
        if src != ReviewState.SUBMISSION_UNCONFIRMED and src != ReviewState.EXTERNAL_APPLICATION_NOT_ATTEMPTED:
             raise ReviewTransitionError(f"Invalid transition to confirmed from {src}")

    if actor == Actor.SYSTEM:
        if dst not in SYSTEM_ALLOWED_TARGETS:
            raise ReviewTransitionError(f"System actor cannot reach {dst}")
