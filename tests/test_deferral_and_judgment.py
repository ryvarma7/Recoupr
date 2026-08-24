"""Timing-deferral judgment + customer_cancelled handling.

The gate's timing violations (quiet_hours, cooldown_active) should RESCHEDULE a
case, not escalate it — waking a human for "it's 2 a.m." is wasted judgment.
Non-timing blocks must still escalate. customer_cancelled must stop cleanly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import app.services.pipeline as pipeline_mod
from sqlmodel import Session, select

from app.core.clock import as_utc
from app.models.entities import Action, CaseState
from app.services.pipeline import process_case
from tests.factories import IST, make_case, make_customer, make_event, make_merchant, make_policy


def _fixture_case(db: Session, *, occurred: datetime):
    """Event + policy + merchant + case created at `occurred` (naive UTC)."""
    customer = make_customer(db)
    event = make_event(
        error_code="gateway_timeout",
        error_description="timed out at bank",
        occurred_at=occurred,
    )
    db.add(event)
    db.flush()
    policy = make_policy()
    db.add(policy)
    db.flush()
    merchant = make_merchant(db, policy)
    return make_case(db, policy, merchant, customer, event, now=occurred - timedelta(days=1))


def _actions(db: Session, case) -> list[Action]:
    return list(db.exec(select(Action).where(Action.case_id == case.id)).all())  # type: ignore[arg-type]


def test_quiet_hours_block_defers_then_executes(db: Session):
    """First contact at 23:30 IST → deferred to 08:00 IST, then executes on wake."""
    # 2026-08-24 23:30 IST == 18:00 UTC — inside the default 21:00–08:00 window.
    quiet_time = datetime(2026, 8, 24, 18, 0)

    case = _fixture_case(db, occurred=quiet_time)
    process_case(db, case, now=quiet_time)

    assert case.state == CaseState.AWAITING_OUTCOME
    assert case.attempts_count == 0, "deferral must not consume an attempt"
    assert case.deferred_until is not None
    resume_local = as_utc(case.deferred_until).astimezone(IST)
    assert (resume_local.hour, resume_local.minute) == (8, 0), "resume at quiet-hours end"
    assert not any(
        a.action_type == "send_payment_link" for a in _actions(db, case)
    ), "nothing may be sent during quiet hours"

    # Wake at/after the resume instant → link goes out.
    process_case(db, case, now=case.deferred_until + timedelta(minutes=5))
    assert case.attempts_count == 1
    assert any(a.action_type == "send_payment_link" for a in _actions(db, case))
    assert case.deferred_until is None


def test_cooldown_block_defers_instead_of_escalating(db: Session):
    """Second attempt 30 min after the first → deferred to cooldown end, not escalated."""
    # 03:00 UTC = 08:30 IST; +6h cooldown resumes at 14:30 IST — both business hours.
    first_contact = datetime(2026, 8, 20, 3, 0)

    case = _fixture_case(db, occurred=first_contact - timedelta(days=1))
    process_case(db, case, now=first_contact)
    assert case.attempts_count == 1

    # Retry wake only 30 minutes later → cooldown_active → defer to +6h.
    too_soon = first_contact + timedelta(minutes=30)
    process_case(db, case, now=too_soon)
    assert case.state == CaseState.AWAITING_OUTCOME
    assert case.attempts_count == 1, "deferred retry consumes nothing"
    assert case.deferred_until is not None
    expected_resume = first_contact + timedelta(hours=6)
    assert abs((case.deferred_until - expected_resume).total_seconds()) < 60

    # After the cooldown the retry really executes.
    process_case(db, case, now=expected_resume + timedelta(minutes=5))
    assert case.attempts_count == 2


def test_non_timing_block_still_escalates(db: Session, monkeypatch):
    """Caps/consent/content refusals are real blocks — escalate, never defer."""
    from app.guardrails.checks import GuardrailVerdict

    case = _fixture_case(db, occurred=datetime(2026, 8, 20, 10, 0))

    def failing_verdict(case_, decision, policy_, ctx):
        return GuardrailVerdict(approved=False, violated_rules=["message_cap_exceeded"])

    monkeypatch.setattr(pipeline_mod, "check_guardrails", failing_verdict)
    process_case(db, case, now=datetime(2026, 8, 19, 10, 0))
    assert case.state == CaseState.ESCALATED_TO_HUMAN
    assert case.deferred_until is None


def test_customer_cancelled_stops_without_messaging(db: Session):
    """An explicit cancellation is respected: stop immediately, zero messages."""
    when = datetime(2026, 8, 21, 15, 0)

    customer = make_customer(db)
    event = make_event(
        error_code="customer_cancelled",
        error_description="payment cancelled by user",
        occurred_at=when,
    )
    db.add(event)
    db.flush()
    policy = make_policy()
    db.add(policy)
    db.flush()
    merchant = make_merchant(db, policy)
    case = make_case(db, policy, merchant, customer, event, now=when)

    process_case(db, case, now=when)
    assert case.state == CaseState.STOPPED_UNRECOVERABLE
    assert case.attempts_count == 0
    assert case.messages_sent_count == 0
    assert case.terminal_reason and "cancelled" in case.terminal_reason.lower()
