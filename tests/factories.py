"""Shared factories for building coherent entity graphs in tests."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, time, timedelta, timezone

from sqlmodel import Session

from app.models.entities import (
    Case,
    Customer,
    Event,
    EventType,
    FlowType,
    GuardrailPolicy,
    Merchant,
)

_EVENT_SEQ = itertools.count(1000)


def make_policy(**overrides) -> GuardrailPolicy:
    """Spec §5.5 defaults; overrides for targeted scenarios."""
    policy = GuardrailPolicy(
        name=overrides.pop("name", "default"),
        max_retries_per_case=3,
        retry_cooldown_hours=6,
        message_cap_per_case=2,
        message_cap_window_days=7,
        quiet_hours_start=time(21, 0),
        quiet_hours_end=time(8, 0),
        allowed_channels=["email", "sms", "whatsapp"],
        consent_required_channels=["sms", "whatsapp"],
    )
    for key, value in overrides.items():
        setattr(policy, key, value)
    return policy


def make_customer(session: Session, **consent) -> Customer:
    customer = Customer(
        masked_phone="+91•••••1234" if consent.pop("with_phone", True) else None,
        masked_email="c•••t@example.com",
        consent_flags={
            "sms_opt_in": consent.pop("sms_opt_in", False),
            "whatsapp_opt_in": consent.pop("whatsapp_opt_in", False),
        },
        locale_pref=consent.pop("locale_pref", "en"),
    )
    session.add(customer)
    session.flush()
    return customer


def make_merchant(session: Session, policy: GuardrailPolicy) -> Merchant:
    merchant = Merchant(name="Meera Candles", guardrail_policy_id=policy.id)
    session.add(merchant)
    session.flush()
    return merchant


def make_event(
    *,
    event_type: EventType = EventType.PAYMENT_FAILED,
    amount: int = 120_000,  # ₹1,200.00
    order_id: str = "order_MC4471",
    subscription_id: str | None = None,
    error_code: str | None = None,
    error_description: str | None = None,
    occurred_at: datetime | None = None,
    ground_truth_recoverable: bool | None = None,
) -> Event:
    return Event(
        source_event_id=f"evt_{next(_EVENT_SEQ)}",
        type=event_type,
        amount=amount,
        order_id=order_id,
        subscription_id=subscription_id,
        customer_id=None,
        error_code=error_code,
        error_description=error_description,
        payload={},
        ground_truth_recoverable=ground_truth_recoverable,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def flow_for(event_type: EventType) -> FlowType:
    return {
        EventType.PAYMENT_FAILED: FlowType.PAYMENT_FAILURE,
        EventType.CHECKOUT_ABANDONED: FlowType.CHECKOUT_ABANDONMENT,
        EventType.SUBSCRIPTION_CHARGE_FAILED: FlowType.SUBSCRIPTION_MANDATE,
        EventType.PAYMENT_CAPTURED: FlowType.PAYMENT_FAILURE,
    }[event_type]


def make_case(
    session: Session,
    policy: GuardrailPolicy,
    merchant: Merchant,
    customer: Customer,
    event: Event,
    **overrides,
) -> Case:
    now = overrides.pop("now", event.occurred_at)
    case = Case(
        display_ref=overrides.pop(
            "display_ref", f"CS-T{abs(hash(event.source_event_id)) % 10**6:06d}"
        ),
        event_id=event.id,  # type: ignore[arg-type]
        merchant_id=merchant.id,  # type: ignore[arg-type]
        flow_type=overrides.pop("flow_type", flow_for(event.type)),
        amount=event.amount,
        order_id=event.order_id,
        subscription_id=event.subscription_id,
        customer_id=customer.id,  # type: ignore[arg-type]
        policy_snapshot=policy.snapshot(),
        case_deadline_at=now + timedelta(days=policy.case_ttl_days),
    )
    for key, value in overrides.items():
        setattr(case, key, value)
    session.add(case)
    session.flush()
    return case


IST = timezone(timedelta(hours=5, minutes=30))
