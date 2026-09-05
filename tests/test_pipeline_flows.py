"""End-to-end pipeline tests through all three flows plus chaos scenarios."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.entities import (
    Action,
    Actor,
    AuditLogEntry,
    Case,
    CaseState,
    DecisionStatus,
    Event,
    EventType,
    GuardrailCheck,
    Outcome,
    OutcomeType,
)
from app.services.pipeline import (
    approve_and_send,
    create_case_for_event,
    mark_lost,
    process_case,
    record_recovery,
)
from tests.factories import (
    make_case,
    make_customer,
    make_event,
    make_merchant,
    make_policy,
)

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)  # 14:30 IST — outside quiet hours


@pytest.fixture(autouse=True)
def _verified_senders(monkeypatch):
    """Demo-mode simulated DLT/WhatsApp registration for pipeline tests."""
    monkeypatch.setenv("SMS_SENDER_VERIFIED", "true")
    monkeypatch.setenv("WHATSAPP_SENDER_VERIFIED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _seed(db: Session):
    policy = make_policy()
    db.add(policy)
    db.flush()
    merchant = make_merchant(db, policy)
    customer = make_customer(db, sms_opt_in=True, whatsapp_opt_in=True)
    return policy, merchant, customer


def _ingest(db: Session, event: Event, *, now=NOW) -> Case:
    db.add(event)
    db.flush()
    policy, merchant, customer = _seed(db)
    return make_case(db, policy, merchant, customer, event, now=now)


def test_flow_a_link_sent_and_recovered(db: Session):
    event = make_event(event_type=EventType.PAYMENT_FAILED, error_code="gateway_timeout",
                       occurred_at=NOW)
    case = _ingest(db, event)
    case = process_case(db, case, now=NOW)

    assert case.state == CaseState.AWAITING_OUTCOME
    assert case.attempts_count == 1                      # incremented on execution
    actions = db.exec(select(Action).where(Action.case_id == case.id)).all()  # type: ignore[arg-type]
    links = [a for a in actions if a.action_type == "send_payment_link"]
    assert len(links) == 1
    assert links[0].status == "EXECUTED"
    assert links[0].external_ref.startswith("plink_")

    # recovery via matched payment
    record_recovery(db, case, payment_id="pay_match001", amount=case.amount,
                    recovered_at=NOW + timedelta(hours=3))
    assert case.state == CaseState.RECOVERED
    outcomes = db.exec(select(Outcome).where(Outcome.case_id == case.id)).all()  # type: ignore[arg-type]
    recovered = [o for o in outcomes if o.outcome_type == OutcomeType.RECOVERED]
    assert len(recovered) == 1
    assert recovered[0].matched_payment_id == "pay_match001"
    assert recovered[0].amount_recovered == case.amount


def test_every_step_writes_audit_rows(db: Session):
    event = make_event(event_type=EventType.PAYMENT_FAILED, error_code="insufficient_funds",
                       occurred_at=NOW)
    case = _ingest(db, event)
    process_case(db, case, now=NOW)

    rows = db.exec(
        select(AuditLogEntry).where(AuditLogEntry.case_id == case.id).order_by(AuditLogEntry.id)  # type: ignore[arg-type]
    ).all()
    summaries = " | ".join(r.summary for r in rows)
    assert "diagnosed" in summaries
    assert "proposed" in summaries
    assert "guardrail check passed" in summaries
    assert "payment link sent" in summaries
    assert all(r.actor in (Actor.AGENT, Actor.SYSTEM) for r in rows)


def test_no_consent_customer_never_messaged_only_email_or_escalation(db: Session):
    """No SMS/WhatsApp consent: agent may use transactional email or escalate — never SMS/WA."""
    from app.models.entities import Decision

    policy = make_policy()
    db.add(policy)
    db.flush()
    merchant = make_merchant(db, policy)
    customer = make_customer(db, sms_opt_in=False, whatsapp_opt_in=False)

    event = make_event(event_type=EventType.PAYMENT_FAILED, error_code="unknown_weird_code_42",
                       error_description="something unusual happened at the bank",
                       occurred_at=NOW)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=NOW)
    process_case(db, case, now=NOW)

    decisions = db.exec(select(Decision).where(Decision.case_id == case.id)).all()  # type: ignore[arg-type]
    for d in decisions:
        channel = d.action_params.get("channel")
        if channel is not None:
            assert channel not in ("sms", "whatsapp"), f"messaging without consent via {channel}"
    # low-confidence diagnosis without LLM → conservative escalation expected
    assert case.state == CaseState.ESCALATED_TO_HUMAN


def test_flow_c_mandate_retry_recovers(db: Session):
    event = make_event(event_type=EventType.SUBSCRIPTION_CHARGE_FAILED,
                       error_code="transaction_processing_error",
                       subscription_id="sub_abc123", occurred_at=NOW)
    case = _ingest(db, event)
    case.subscription_id = "sub_abc123"
    process_case(db, case, now=NOW)

    assert case.state == CaseState.AWAITING_OUTCOME
    actions = db.exec(select(Action).where(Action.case_id == case.id)).all()  # type: ignore[arg-type]
    retries = [a for a in actions if a.action_type == "retry_mandate_charge"]
    assert len(retries) == 1 and retries[0].status == "EXECUTED"
    # Flows A/B must NEVER produce a direct charge — only Flow C does:
    links = [a for a in actions if a.action_type == "send_payment_link"]
    assert links == []


def test_policy_snapshot_survives_live_policy_edit(db: Session):
    from datetime import time as dtime

    policy, merchant, customer = _seed(db)
    event = make_event(error_code="gateway_timeout", occurred_at=NOW)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=NOW)

    # Merchant edits the live policy mid-case…
    policy.max_retries_per_case = 99
    policy.quiet_hours_start = dtime(0, 0)
    db.add(policy)
    db.flush()

    # …the in-flight case keeps running against its snapshot.
    assert case.policy_snapshot["max_retries_per_case"] == 3
    assert case.policy_snapshot["quiet_hours_start"] == "21:00:00"


def test_llm_timeout_falls_back_to_escalate_never_fail_open(db: Session, monkeypatch):
    from app.agents.llm import LLMTimedOut

    policy, merchant, customer = _seed(db)
    # No known error code → forces the LLM path; we simulate a timeout.
    event = make_event(error_code="weird_unknown_failure", error_description="unclear bank mumble",
                       occurred_at=NOW)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=NOW)

    def explode(*args, **kwargs):
        raise LLMTimedOut("simulated timeout")

    class TimeoutLLM:
        available = True
        classify = staticmethod(explode)

    monkeypatch.setattr("app.agents.diagnosis.get_llm", lambda: TimeoutLLM())
    process_case(db, case, now=NOW)

    assert case.state == CaseState.ESCALATED_TO_HUMAN
    assert "diagnosis unavailable" in (case.terminal_reason or "")
    actions = db.exec(select(Action).where(Action.case_id == case.id)).all()  # type: ignore[arg-type]
    assert not [a for a in actions if a.status == "EXECUTED" and a.action_type != "escalate_human"]


def test_two_cases_on_one_order_are_linked(db: Session):
    first = make_event(order_id="order_shared99", error_code="gateway_timeout", occurred_at=NOW)
    db.add(first)
    db.flush()
    case1 = create_case_for_event(db, first, now=NOW)

    second = make_event(order_id="order_shared99", error_code="card_expired", occurred_at=NOW)
    db.add(second)
    db.flush()
    case2 = create_case_for_event(db, second, now=NOW)

    assert case1.id in case2.related_case_ids
    assert case2.id in case1.related_case_ids


def test_quiet_hours_block_defers_and_consumes_no_attempt(db: Session):
    """Quiet-hours proposal is deferred to morning; nothing sent, no attempt used."""
    policy, merchant, customer = _seed(db)
    late_night = datetime(2026, 8, 24, 17, 30, tzinfo=UTC)  # 23:00 IST — quiet
    event = make_event(error_code="gateway_timeout", occurred_at=late_night)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=late_night)
    process_case(db, case, now=late_night)

    assert case.attempts_count == 0
    assert case.state == CaseState.AWAITING_OUTCOME, "deferred, not escalated"
    assert case.deferred_until is not None
    checks = db.exec(select(GuardrailCheck).where(GuardrailCheck.case_id == case.id)).all()  # type: ignore[arg-type]
    assert any(not c.passed and "quiet_hours" in c.violated_rules for c in checks)
    from app.models.entities import Decision

    blocked_decisions = db.exec(select(Decision)).all()
    assert any(d.status == DecisionStatus.BLOCKED for d in blocked_decisions)


def test_ttl_sweep_marks_lost(db: Session):
    policy, merchant, customer = _seed(db)
    event = make_event(error_code="gateway_timeout", occurred_at=NOW)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, state=CaseState.AWAITING_OUTCOME, now=NOW)
    process_case(db, case, now=NOW)  # executes → AWAITING_OUTCOME
    assert case.attempts_count == 1

    mark_lost(db, case, now=case.case_deadline_at + timedelta(seconds=1))
    assert case.state == CaseState.LOST
    assert case.terminal_reason is not None


def test_approve_and_send_from_escalated_state(db: Session):
    # Higher message cap than default so three link sends fit before the cap;
    # default cap of 2 would escalate on message_cap_exceeded instead.
    policy = make_policy(message_cap_per_case=5)
    db.add(policy)
    db.flush()
    customer = make_customer(db)
    merchant = make_merchant(db, policy)
    # Drive to escalation the honest way: three executions then a fourth wake —
    # retries exhausted → escalate_human (gate-exempt, so timing never blocks it).
    start = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)   # 08:30 IST — business hours
    event = make_event(error_code="gateway_timeout", occurred_at=start - timedelta(days=1))
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=start)

    process_case(db, case, now=start)                                     # attempt 1 (08:30 IST)
    process_case(db, case, now=start + timedelta(hours=6, minutes=5))     # attempt 2 (14:35 IST)
    process_case(db, case, now=start + timedelta(hours=12, minutes=6))    # attempt 3 (20:36 IST)
    assert case.attempts_count == 3 and case.state == CaseState.AWAITING_OUTCOME

    process_case(db, case, now=start + timedelta(hours=18, minutes=15))
    assert case.state == CaseState.ESCALATED_TO_HUMAN

    approved = approve_and_send(db, case, now=start + timedelta(hours=18, minutes=45))
    assert approved.state == CaseState.AWAITING_OUTCOME
    approved_action = db.exec(
        select(Action).where(Action.case_id == case.id, Action.action_type == "send_payment_link")
        .order_by(Action.id.desc())
    ).first()
    assert approved_action is not None and approved_action.external_ref.startswith("plink_")
    assert case.attempts_count == 4
    assert case.last_action_at == (start + timedelta(hours=18, minutes=45)).replace(tzinfo=None)
    human_rows = db.exec(
        select(AuditLogEntry).where(AuditLogEntry.case_id == case.id)  # type: ignore[arg-type]
    ).all()
    assert any(r.actor == Actor.HUMAN for r in human_rows)


def test_wrong_amount_payment_is_rejected(db: Session):
    policy, merchant, customer = _seed(db)
    event = make_event(error_code="gateway_timeout", occurred_at=NOW)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=NOW)
    process_case(db, case, now=NOW)
    with pytest.raises(ValueError, match="does not exactly match"):
        record_recovery(db, case, payment_id="pay_wrong", amount=case.amount + 1, recovered_at=NOW)
    assert case.state == CaseState.AWAITING_OUTCOME


def test_final_rendered_message_is_scanned(db: Session, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    policy, merchant, customer = _seed(db)
    event = make_event(error_code="gateway_timeout", occurred_at=NOW)
    db.add(event)
    db.flush()
    case = make_case(db, policy, merchant, customer, event, now=NOW)
    monkeypatch.setattr("app.services.pipeline.render_recovery_message", lambda **_: "Please send your OTP here")
    process_case(db, case, now=NOW)
    assert case.state == CaseState.ESCALATED_TO_HUMAN
    assert any(
        any("sensitive_content" in rule for rule in (c.violated_rules or []))
        for c in db.exec(select(GuardrailCheck)).all()
    )
    get_settings.cache_clear()
