"""State machine transition tests — illegal transitions must raise, never no-op."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agents.state_machine import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    assert_transition,
)
from app.models.entities import Actor, Case, CaseState

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


class FakeCase:
    """Minimal duck-typed Case for pure transition assertions (no DB needed)."""

    def __init__(self, state: CaseState):
        self.state = state
        self.id = 1


def _transition(target: CaseState, current: CaseState) -> None:
    assert_transition(current, target)


def test_happy_path_through_every_legal_transition():
    case_states = [
        (CaseState.NEW, CaseState.PROCESSING),
        (CaseState.PROCESSING, CaseState.AWAITING_OUTCOME),
        (CaseState.AWAITING_OUTCOME, CaseState.RECOVERED),
    ]
    for current, target in case_states:
        assert_transition(current, target)  # must not raise


def test_retry_loop_back_is_legal():
    assert_transition(CaseState.AWAITING_OUTCOME, CaseState.PROCESSING)


@pytest.mark.parametrize(
    "current,target",
    [
        # the spec's canonical illegal transition
        (CaseState.RECOVERED, CaseState.PROCESSING),
        (CaseState.RECOVERED, CaseState.LOST),
        (CaseState.RECOVERED, CaseState.RECOVERED),
        (CaseState.LOST, CaseState.PROCESSING),
        (CaseState.LOST, CaseState.RECOVERED),
        (CaseState.STOPPED_UNRECOVERABLE, CaseState.PROCESSING),
        (CaseState.NEW, CaseState.AWAITING_OUTCOME),
        (CaseState.NEW, CaseState.RECOVERED),
        (CaseState.PROCESSING, CaseState.RECOVERED),   # can't recover without awaiting outcome
        (CaseState.PROCESSING, CaseState.LOST),
        (CaseState.ESCALATED_TO_HUMAN, CaseState.PROCESSING),  # human approval goes via AWAITING_OUTCOME
    ],
)
def test_illegal_transitions_raise(current, target):
    with pytest.raises(IllegalTransitionError):
        _transition(target, current)


def test_every_terminal_state_has_no_exits():
    for terminal in (
        CaseState.RECOVERED,
        CaseState.LOST,
        CaseState.STOPPED_UNRECOVERABLE,
    ):
        assert ALLOWED_TRANSITIONS[terminal] == set()


def test_escalated_has_exactly_one_exit_via_human_approval():
    assert ALLOWED_TRANSITIONS[CaseState.ESCALATED_TO_HUMAN] == {CaseState.AWAITING_OUTCOME}


def test_transition_writes_audit_row(db):
    from sqlmodel import select

    from app.models.entities import AuditLogEntry

    case = Case(
        display_ref="CS-900001",
        event_id=1,
        merchant_id=1,
        flow_type="A",
        amount=100_000,
        customer_id=1,
        policy_snapshot={},
        state=CaseState.NEW,
    )
    db.add(case)
    db.flush()

    from app.agents.state_machine import transition

    transition(
        db,
        case,
        CaseState.PROCESSING,
        actor=Actor.SYSTEM,
        summary="case picked up by pipeline",
        now=NOW,
    )
    rows = db.exec(select(AuditLogEntry)).all()
    assert len(rows) == 1
    assert rows[0].before_state == "NEW"
    assert rows[0].after_state == "PROCESSING"
    assert rows[0].actor == Actor.SYSTEM


def test_illegal_transition_raises_before_mutating_case(db):
    from app.agents.state_machine import transition

    case = Case(
        display_ref="CS-900002",
        event_id=1,
        merchant_id=1,
        flow_type="A",
        amount=100_000,
        customer_id=1,
        policy_snapshot={},
        state=CaseState.RECOVERED,
    )
    db.add(case)
    db.flush()

    with pytest.raises(IllegalTransitionError):
        transition(
            db,
            case,
            CaseState.PROCESSING,
            actor=Actor.AGENT,
            summary="should never happen",
            now=NOW,
        )
    assert case.state == CaseState.RECOVERED  # unchanged
