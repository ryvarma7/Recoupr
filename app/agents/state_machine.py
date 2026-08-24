"""Hand-rolled case lifecycle state machine — spec §5.2.

No graph framework by design: every transition is inspectable here.

Allowed transitions:

    NEW ──► PROCESSING ──► AWAITING_OUTCOME ──► RECOVERED | LOST | back to PROCESSING
                │                                   (retry loop-back, attempts < max)
                ├──► ESCALATED_TO_HUMAN
                └──► STOPPED_UNRECOVERABLE

Documented deviation from the spec table: ESCALATED_TO_HUMAN has exactly one exit —
AWAITING_OUTCOME via an explicit human approval (actor=human, audit-logged). The
dashboard's "Approve & Send" action requires it; without it an approved-and-paid
escalation could never be observed as a recovery. RECOVERED is truly terminal —
nothing exits it, ever.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from app.models.entities import (
    Actor,
    AuditLogEntry,
    Case,
    CaseState,
)

ALLOWED_TRANSITIONS: dict[CaseState, set[CaseState]] = {
    CaseState.NEW: {CaseState.PROCESSING},
    CaseState.PROCESSING: {
        CaseState.AWAITING_OUTCOME,
        CaseState.ESCALATED_TO_HUMAN,
        CaseState.STOPPED_UNRECOVERABLE,
    },
    CaseState.AWAITING_OUTCOME: {
        CaseState.PROCESSING,
        CaseState.RECOVERED,
        CaseState.LOST,
    },
    CaseState.ESCALATED_TO_HUMAN: {
        CaseState.AWAITING_OUTCOME,  # human approval only — see module docstring
    },
    # Terminal states with no exits:
    CaseState.RECOVERED: set(),
    CaseState.LOST: set(),
    CaseState.STOPPED_UNRECOVERABLE: set(),
}


class IllegalTransitionError(RuntimeError):
    pass


def assert_transition(current: CaseState, target: CaseState) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise IllegalTransitionError(f"illegal case transition {current.value} → {target.value}")


def transition(
    session: Session,
    case: Case,
    target: CaseState,
    *,
    actor: Actor,
    summary: str,
    now: datetime,
) -> Case:
    """Apply a transition, raising on anything illegal — never silently no-op."""
    assert_transition(case.state, target)
    before = case.state
    case.state = target
    case.updated_at = now
    audit(session, case, actor=actor, summary=summary, before=before, after=target, now=now)
    return case


def audit(
    session: Session,
    case: Case,
    *,
    actor: Actor,
    summary: str,
    before: str | CaseState | None = None,
    after: str | CaseState | None = None,
    now: datetime,
) -> AuditLogEntry:
    """Append one immutable audit row. Every meaningful step goes through here."""
    entry = AuditLogEntry(
        case_id=case.id,  # type: ignore[arg-type]
        actor=actor,
        summary=summary[:500],
        before_state=_state_name(before),
        after_state=_state_name(after),
        timestamp=now,
    )
    session.add(entry)
    return entry


def _state_name(value: str | CaseState | None) -> str | None:
    if value is None:
        return None
    return value.value if isinstance(value, CaseState) else str(value)
