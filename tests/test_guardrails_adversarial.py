"""Adversarial guardrail suite — tries to break EVERY rule at once, and asserts
the gate catches all of them. This is the safety claim of the project."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.guardrails.checks import GateContext, check_guardrails
from app.guardrails.policy import PolicySnapshot
from app.models.entities import (
    Case,
    CaseState,
    Decision,
    EventType,
    FlowType,
)
from tests.factories import (
    make_case,
    make_customer,
    make_event,
    make_merchant,
    make_policy,
)

NOON_IST = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)  # 17:30 IST — outside quiet hours


def _gate_context(**overrides) -> GateContext:
    defaults = dict(
        now=NOON_IST,
        tz=ZoneInfo("Asia/Kolkata"),
        customer_sms_opt_in=True,
        customer_whatsapp_opt_in=True,
        verified_channels=frozenset({"sms", "whatsapp", "email"}),
        messages_sent_at=(),
    )
    defaults.update(overrides)
    return GateContext(**defaults)


def _clean_link_decision(case: Case, **param_overrides) -> Decision:
    """A decision that passes every rule — the baseline each adversarial test mutates."""
    params = {
        "channel": "whatsapp",
        "language": "en",
        "tone": "friendly",
        "template_id": "tpl_recovery_v1",
        "amount": case.amount,
        "order_ref": case.order_id or "order_X",
        "expire_hours": 24,
        "single_use": True,
        "hosted_domain": "rzp.io",
        "message_body": f"Order {case.order_id} for ₹1,200 didn't go through — pay securely here.",
    }
    params.update(param_overrides)
    return Decision(
        case_id=case.id,  # type: ignore[arg-type]
        proposed_action="send_payment_link",
        action_params=params,
        message_language="en",
        message_tone="friendly",
        reasoning="recoverable bank timeout; nudge with fresh link",
    )


def make_flow_case(
    session: Session,
    *,
    flow: FlowType = FlowType.PAYMENT_FAILURE,
    state: CaseState = CaseState.PROCESSING,
    attempts: int = 0,
    last_action_at: datetime | None = None,
    now: datetime = NOON_IST,
    policy=None,
    event_type: EventType | None = None,
) -> Case:
    policy = policy or make_policy()
    session.add(policy)
    session.flush()
    merchant = make_merchant(session, policy)
    customer = make_customer(session, sms_opt_in=True, whatsapp_opt_in=True)

    if event_type is None:
        event_type = {
            FlowType.PAYMENT_FAILURE: EventType.PAYMENT_FAILED,
            FlowType.CHECKOUT_ABANDONMENT: EventType.CHECKOUT_ABANDONED,
            FlowType.SUBSCRIPTION_MANDATE: EventType.SUBSCRIPTION_CHARGE_FAILED,
        }[flow]

    sub_id = "sub_111" if flow == FlowType.SUBSCRIPTION_MANDATE else None
    event = make_event(event_type=event_type, subscription_id=sub_id)
    session.add(event)
    session.flush()
    return make_case(
        session,
        policy,
        merchant,
        customer,
        event,
        flow_type=flow,
        state=state,
        attempts_count=attempts,
        last_action_at=last_action_at,
        now=now,
    )


# ---------------------------------------------------------------------------
# The one-decision-breaks-everything test
# ---------------------------------------------------------------------------

def test_gate_catches_every_violation_in_a_single_proposal(db: Session):
    restricted_policy = make_policy(allowed_channels=["email"])   # sms → channel_not_allowed
    case = make_flow_case(
        db,
        flow=FlowType.SUBSCRIPTION_MANDATE,          # → action_flow_mismatch for a link send
        attempts=3,                                   # → retry_cap_exceeded (cap is 3)
        last_action_at=NOON_IST - timedelta(minutes=30),  # → cooldown_active (6h cooldown)
        now=NOON_IST - timedelta(days=20),            # created long ago…
        policy=restricted_policy,
    )
    case.case_deadline_at = NOON_IST - timedelta(days=1)  # → case_ttl_expired

    decision = Decision(
        case_id=case.id,
        proposed_action="send_payment_link",
        action_params={
            "channel": "sms",                          # allowed? no → channel_not_allowed:sms
            "amount": case.amount + 5_000,             # → amount_mutation
            "order_ref": "",                           # → order_reference_missing
            "expire_hours": 1,                         # → link_expiry_invalid (policy: 24)
            "single_use": False,                       # → link_not_single_use
            "hosted_domain": "pay-recoveries-evil.example",  # → link_domain_untrusted
            "message_body": "Please reply with your OTP to verify — enter card number if asked.",
        },                                             # → sensitive_content_in_message
        reasoning="adversarial proposal",
    )

    ctx = _gate_context(
        customer_sms_opt_in=False,                     # → consent_missing:sms
        verified_channels=frozenset({"email"}),        # → sender_unverified:sms
        messages_sent_at=(                             # cap is 2 per 7 days:
            NOON_IST - timedelta(days=2),
            NOON_IST - timedelta(days=3),
        ),                                             # → message_cap_exceeded
    )
    # and the proposal lands inside quiet hours:
    ctx_now = NOON_IST.replace(hour=17, minute=0)  # 22:30 IST → quiet hours
    ctx = GateContext(**{**ctx.__dict__, "now": ctx_now})

    verdict = check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), ctx)

    expected = {
        "action_flow_mismatch",
        "retry_cap_exceeded",
        "case_ttl_expired",
        "cooldown_active",
        "amount_mutation",
        "channel_not_allowed:sms",
        "consent_missing:sms",
        "sender_unverified:sms",
        "quiet_hours",
        "message_cap_exceeded",
        "link_expiry_invalid",
        "link_not_single_use",
        "link_domain_untrusted",
        "order_reference_missing",
        "sensitive_content_in_message",
    }
    assert not verdict.approved
    assert set(verdict.violated_rules) == expected, (
        f"missing: {expected - set(verdict.violated_rules)}, "
        f"unexpected: {set(verdict.violated_rules) - expected}"
    )
    assert len(verdict.violated_rules) == len(expected)


def test_blocked_proposal_never_touches_attempts_count(db: Session):
    """The gate is pure: a block must not consume an attempt."""
    case = make_flow_case(db, attempts=0)
    decision = _clean_link_decision(case, expire_hours=99)
    verdict = check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), _gate_context())
    assert not verdict.approved
    assert case.attempts_count == 0


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------

def test_quiet_hours_boundaries(db: Session):
    case = make_flow_case(db)
    decision = _clean_link_decision(case, channel="email")

    def verdict_at(hour_utc: int, minute: int = 0):
        now = datetime(2026, 8, 24, hour_utc, minute, tzinfo=UTC)
        ctx = _gate_context(now=now, verified_channels=frozenset({"email", "sms", "whatsapp"}))
        return check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), ctx)

    # 21:00–08:00 IST quiet window (15:30–02:30 UTC). Boundaries are [start, end).
    assert "quiet_hours" in verdict_at(16, 0).violated_rules       # 21:30 IST
    assert "quiet_hours" in verdict_at(20, 29).violated_rules      # 01:59 IST
    assert "quiet_hours" not in verdict_at(15, 29).violated_rules  # 20:59 IST
    assert "quiet_hours" not in verdict_at(2, 30).violated_rules   # 08:00 IST


def test_retry_cap_boundary(db: Session):
    at_limit = make_flow_case(db, attempts=3)
    decision = _clean_link_decision(at_limit)
    verdict = check_guardrails(at_limit, decision, PolicySnapshot(at_limit.policy_snapshot), _gate_context())
    assert "retry_cap_exceeded" in verdict.violated_rules


def test_cooldown_boundary(db: Session):
    almost_ready = make_flow_case(
        db, attempts=1, last_action_at=NOON_IST - timedelta(hours=6)
    )
    decision = _clean_link_decision(almost_ready)
    verdict = check_guardrails(
        almost_ready, decision, PolicySnapshot(almost_ready.policy_snapshot), _gate_context()
    )
    assert "cooldown_active" not in verdict.violated_rules  # exactly 6h has elapsed

    too_soon = make_flow_case(
        db, attempts=1, last_action_at=NOON_IST - timedelta(hours=6) + timedelta(seconds=1)
    )
    verdict = check_guardrails(
        too_soon, decision, PolicySnapshot(too_soon.policy_snapshot), _gate_context()
    )
    assert "cooldown_active" in verdict.violated_rules


def test_message_cap_window_only_counts_recent_messages(db: Session):
    case = make_flow_case(db)
    old_messages = (NOON_IST - timedelta(days=8), NOON_IST - timedelta(days=9))
    decision = _clean_link_decision(case)
    verdict = check_guardrails(
        case,
        decision,
        PolicySnapshot(case.policy_snapshot),
        _gate_context(messages_sent_at=old_messages),
    )
    assert "message_cap_exceeded" not in verdict.violated_rules  # outside the 7-day window

    recent_messages = (NOON_IST - timedelta(days=1), NOON_IST - timedelta(days=2))
    verdict = check_guardrails(
        case,
        decision,
        PolicySnapshot(case.policy_snapshot),
        _gate_context(messages_sent_at=recent_messages),
    )
    assert "message_cap_exceeded" in verdict.violated_rules


def test_email_is_transactional_and_never_consent_gated(db: Session):
    case = make_flow_case(db)
    decision = _clean_link_decision(case, channel="email")
    verdict = check_guardrails(
        case,
        decision,
        PolicySnapshot(case.policy_snapshot),
        _gate_context(customer_sms_opt_in=False, customer_whatsapp_opt_in=False),
    )
    assert verdict.approved, verdict.violated_rules


# ---------------------------------------------------------------------------
# Escalation / stop must stay available even when everything else is blocked
# ---------------------------------------------------------------------------

def test_escalation_not_blocked_by_exhausted_retries_or_ttl(db: Session):
    case = make_flow_case(db, attempts=3)
    case.case_deadline_at = NOON_IST - timedelta(days=1)

    decision = Decision(
        case_id=case.id,
        proposed_action="escalate_human",
        action_params={"reason": "retries exhausted on unrecoverable card"},
    )
    verdict = check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), _gate_context())
    assert verdict.approved, verdict.violated_rules


def test_stop_is_always_permitted(db: Session):
    case = make_flow_case(db, attempts=3)
    decision = Decision(case_id=case.id, proposed_action="stop", action_params={"reason": "no path"})
    verdict = check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), _gate_context())
    assert verdict.approved


def test_escalation_requires_a_reason(db: Session):
    case = make_flow_case(db)
    decision = Decision(case_id=case.id, proposed_action="escalate_human", action_params={})
    verdict = check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), _gate_context())
    assert not verdict.approved
    assert "escalation_reason_missing" in verdict.violated_rules


def test_unknown_action_flagged(db: Session):
    case = make_flow_case(db)
    decision = Decision(case_id=case.id, proposed_action="charge_card_directly", action_params={})
    verdict = check_guardrails(case, decision, PolicySnapshot(case.policy_snapshot), _gate_context())
    assert not verdict.approved
    assert "unknown_action" in verdict.violated_rules


# ---------------------------------------------------------------------------
# Flow/action authority split
# ---------------------------------------------------------------------------

def test_flows_a_and_b_can_send_links_but_c_cannot(db: Session):
    def link_verdict(flow):
        case = make_flow_case(db, flow=flow)
        return check_guardrails(
            case, _clean_link_decision(case), PolicySnapshot(case.policy_snapshot), _gate_context(),
        )

    for flow in (FlowType.PAYMENT_FAILURE, FlowType.CHECKOUT_ABANDONMENT):
        verdict = link_verdict(flow)
        assert verdict.approved, (flow, verdict.violated_rules)

    verdict_c = link_verdict(FlowType.SUBSCRIPTION_MANDATE)
    assert not verdict_c.approved
    assert "action_flow_mismatch" in verdict_c.violated_rules


def test_mandate_retry_allowed_on_c_only(db: Session):
    case_c = make_flow_case(db, flow=FlowType.SUBSCRIPTION_MANDATE)
    mandate_decision = Decision(
        case_id=case_c.id,
        proposed_action="retry_mandate_charge",
        action_params={"amount": case_c.amount},
    )
    ctx = _gate_context(now=datetime(2026, 8, 25, 0, 0, tzinfo=UTC))  # 05:30 IST — deep quiet hours
    verdict = check_guardrails(case_c, mandate_decision, PolicySnapshot(case_c.policy_snapshot), ctx)
    assert verdict.approved, verdict.violated_rules  # agent-side retry ignores quiet hours by design

    for flow in (FlowType.PAYMENT_FAILURE, FlowType.CHECKOUT_ABANDONMENT):
        case = make_flow_case(db, flow=flow)
        mismatch = check_guardrails(case, mandate_decision, PolicySnapshot(case.policy_snapshot), _gate_context())
        assert "action_flow_mismatch" in mismatch.violated_rules


# ---------------------------------------------------------------------------
# Purity of the gate
# ---------------------------------------------------------------------------

def test_gate_is_pure_and_deterministic(db: Session):
    case = make_flow_case(db)
    decision = _clean_link_decision(case, single_use=False)
    snapshot = PolicySnapshot(case.policy_snapshot)
    ctx = _gate_context()

    first = check_guardrails(case, decision, snapshot, ctx)
    second = check_guardrails(case, decision, snapshot, ctx)
    assert first.violated_rules == second.violated_rules
    assert case.attempts_count == 0
    assert case.state == CaseState.PROCESSING
