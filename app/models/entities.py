"""SQLModel entities — spec §5.3.

Notes
-----
- Money is stored as integer paise (Razorpay's native unit); formatting to ₹ is a
  presentation concern.
- JSON columns are used for policy_snapshot / violated_rules / consent_flags so
  the schema stays dialect-agnostic (SQLite in tests, Postgres 16 in compose).
- Event.source_event_id carries a unique constraint: duplicate webhook deliveries
  fail the insert and are treated as idempotent no-ops upstream.
- AuditLogEntry has no update/delete path anywhere in the codebase; the session
  guard in app/db/session.py raises if ORM flush ever attempts one.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime, time

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EventType(str, enum.Enum):
    PAYMENT_FAILED = "payment.failed"
    CHECKOUT_ABANDONED = "checkout.abandoned"
    SUBSCRIPTION_CHARGE_FAILED = "subscription.charge.failed"
    PAYMENT_CAPTURED = "payment.captured"


class CaseState(str, enum.Enum):
    NEW = "NEW"
    PROCESSING = "PROCESSING"
    AWAITING_OUTCOME = "AWAITING_OUTCOME"
    RECOVERED = "RECOVERED"
    LOST = "LOST"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    STOPPED_UNRECOVERABLE = "STOPPED_UNRECOVERABLE"


TERMINAL_STATES = {
    CaseState.RECOVERED,
    CaseState.LOST,
    CaseState.ESCALATED_TO_HUMAN,
    CaseState.STOPPED_UNRECOVERABLE,
}


class FlowType(str, enum.Enum):
    PAYMENT_FAILURE = "A"          # payment.failed → send_payment_link
    CHECKOUT_ABANDONMENT = "B"     # checkout.abandoned → send_payment_link
    SUBSCRIPTION_MANDATE = "C"     # subscription.charge.failed → retry_mandate_charge


class Actor(str, enum.Enum):
    AGENT = "agent"
    SYSTEM = "system"
    HUMAN = "human"


class Merchant(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    razorpay_key_id: str = ""          # test-mode key id; empty → mock client
    guardrail_policy_id: int | None = Field(default=None, foreign_key="guardrailpolicy.id")
    timezone: str = "Asia/Kolkata"
    created_at: datetime = Field(default_factory=_utcnow)


class GuardrailPolicy(SQLModel, table=True):
    """Defaults are the spec §2.5 shippable numbers — use exactly these."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = "default"
    max_retries_per_case: int = 3
    retry_cooldown_hours: int = 6
    message_cap_per_case: int = 2
    message_cap_window_days: int = 7
    quiet_hours_start: time = time(21, 0)
    quiet_hours_end: time = time(8, 0)
    allowed_channels: list[str] = Field(default_factory=lambda: ["email", "sms", "whatsapp"], sa_column=Column(JSON))
    consent_required_channels: list[str] = Field(
        default_factory=lambda: ["sms", "whatsapp"], sa_column=Column(JSON)
    )
    amount_immutability: bool = True   # always true — column exists so the gate reads it from the snapshot
    case_ttl_days: int = 14
    payment_link_expiry_hours: int = 24
    payment_link_single_use: bool = True
    created_at: datetime = Field(default_factory=_utcnow)

    def snapshot(self) -> dict:
        """Immutable copy attached to each Case at creation time."""
        return {
            "id": self.id,
            "name": self.name,
            "max_retries_per_case": self.max_retries_per_case,
            "retry_cooldown_hours": self.retry_cooldown_hours,
            "message_cap_per_case": self.message_cap_per_case,
            "message_cap_window_days": self.message_cap_window_days,
            "quiet_hours_start": self.quiet_hours_start.isoformat(),
            "quiet_hours_end": self.quiet_hours_end.isoformat(),
            "allowed_channels": list(self.allowed_channels),
            "consent_required_channels": list(self.consent_required_channels),
            "amount_immutability": self.amount_immutability,
            "case_ttl_days": self.case_ttl_days,
            "payment_link_expiry_hours": self.payment_link_expiry_hours,
            "payment_link_single_use": self.payment_link_single_use,
        }


class Customer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    masked_phone: str | None = None       # e.g. "+91•••••4321"
    masked_email: str | None = None       # e.g. "r•••l@example.com"
    upi_vpa_hash: str | None = None
    consent_flags: dict = Field(default_factory=dict, sa_column=Column(JSON))
    locale_pref: str = "en"

    @property
    def sms_opt_in(self) -> bool:
        return bool(self.consent_flags.get("sms_opt_in", False))

    @property
    def whatsapp_opt_in(self) -> bool:
        return bool(self.consent_flags.get("whatsapp_opt_in", False))


class Event(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source_event_id", name="uq_event_source_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source_event_id: str = Field(index=True)
    type: EventType
    razorpay_payload_ref: str | None = None
    amount: int                          # paise
    currency: str = "INR"
    order_id: str | None = Field(default=None, index=True)
    subscription_id: str | None = Field(default=None, index=True)
    customer_id: int | None = Field(default=None, foreign_key="customer.id")
    error_code: str | None = None
    error_description: str | None = None
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    ground_truth_recoverable: bool | None = None   # set by the synthetic generator only; NULL for live webhooks
    occurred_at: datetime = Field(default_factory=_utcnow)   # event's own timeline timestamp
    received_at: datetime = Field(default_factory=_utcnow)


class Case(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    display_ref: str = Field(unique=True)          # e.g. "CS-000042"
    event_id: int = Field(foreign_key="event.id")
    merchant_id: int = Field(foreign_key="merchant.id")
    flow_type: FlowType
    state: CaseState = CaseState.NEW
    attempts_count: int = 0                        # increments on execution only
    messages_sent_count: int = 0                   # for the per-case message cap
    amount: int                                    # paise — immutable for the case lifetime
    order_id: str | None = Field(default=None, index=True)
    subscription_id: str | None = Field(default=None, index=True)
    customer_id: int = Field(foreign_key="customer.id")
    policy_snapshot: dict = Field(sa_column=Column(JSON))
    related_case_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    terminal_reason: str | None = None
    last_action_at: datetime | None = None         # drives cooldown checks
    case_deadline_at: datetime | None = None       # created + TTL from snapshot
    deferred_until: datetime | None = None         # timing-block retry window (naive UTC)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Diagnosis(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    root_cause_category: str
    confidence: float
    method: str                                    # "rule" | "llm" | "fallback"
    reasoning: str = Field(max_length=280)
    created_at: datetime = Field(default_factory=_utcnow)


class DecisionStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    BLOCKED = "BLOCKED"


class Decision(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    proposed_action: str                           # send_payment_link | retry_mandate_charge | escalate_human | stop
    action_params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    message_language: str | None = None            # en | hi | hinglish
    message_tone: str | None = None                # formal | friendly | urgent
    reasoning: str = Field(max_length=280)
    status: DecisionStatus = DecisionStatus.PROPOSED
    created_at: datetime = Field(default_factory=_utcnow)


class GuardrailCheck(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    decision_id: int = Field(foreign_key="decision.id", index=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    passed: bool
    violated_rules: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # ALL broken rules
    created_at: datetime = Field(default_factory=_utcnow)


class Action(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    decision_id: int | None = Field(default=None, foreign_key="decision.id")  # null for escalations (no decision)
    case_id: int = Field(foreign_key="case.id", index=True)
    action_type: str
    status: str                                    # EXECUTED | FAILED | PENDING_APPROVAL
    actor: Actor = Actor.AGENT
    external_ref: str | None = None                # e.g. Razorpay payment link id
    message_body: str | None = None                # final rendered message (auditable content scan target)
    error: str | None = None
    executed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class OutcomeType(str, enum.Enum):
    RECOVERED = "RECOVERED"
    LOST = "LOST"
    STOPPED = "STOPPED"
    ESCALATED = "ESCALATED"


class Outcome(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    outcome_type: OutcomeType
    amount_recovered: int = 0                      # paise; >0 only with a matched payment
    recovered_at: datetime | None = None
    matched_payment_id: str | None = None          # never null when outcome_type == RECOVERED
    detail: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class AuditLogEntry(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("id", name="uq_auditlog_id"),)

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="case.id", index=True)
    actor: Actor
    summary: str
    before_state: str | None = None
    after_state: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


# Ground-truth labels recorded per synthetic event, for honest false-positive metrics.
class SimulationRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    seed: int | None = None
    cases_requested: int
    cases_created: int = 0
    report: dict = Field(default_factory=dict, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None


def today() -> date:
    return _utcnow().date()
