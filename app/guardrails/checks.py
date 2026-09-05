"""The guardrail gate — plain deterministic code, no LLM anywhere in its path.

check_guardrails() is a PURE function: every input arrives as data (case,
proposed decision, policy snapshot view, gate context assembled by the caller).
No database access, no network, no model calls. It returns ALL violated rules,
not just the first one hit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.state_machine import CaseState
from app.guardrails.policy import PolicySnapshot, in_quiet_hours
from app.models.entities import Case, Decision, FlowType

# Channels whose sender identity must be verified in config (simulated DLT /
# WhatsApp Business registration in demo mode). Email is transactional, exempt.
IDENTITY_VERIFIED_CHANNELS = {"sms", "whatsapp"}

TRUSTED_LINK_DOMAINS = {"rzp.io", "rzp.io/i", "api.razorpay.com"}

_SENSITIVE_CONTENT_RE = re.compile(
    r"(?i)\b(otp|one[\s-]?time[\s-]?password|pin\b|card[\s-]?number|cvv|cvc)\b"
)

def has_sensitive_content(body: str) -> bool:
    return bool(_SENSITIVE_CONTENT_RE.search(body))


@dataclass(frozen=True)
class GateContext:
    """Everything the gate needs, gathered by the caller before invocation."""

    now: datetime
    tz: ZoneInfo
    customer_sms_opt_in: bool = False
    customer_whatsapp_opt_in: bool = False
    verified_channels: frozenset[str] = frozenset()
    # Timestamps of messages already sent on this case (for the windowed cap):
    messages_sent_at: tuple[datetime, ...] = ()
    # True when this proposal follows a human approval (approval re-check skips
    # nothing by default; kept for future bounded re-proposal work).
    human_approved: bool = False


@dataclass
class GuardrailVerdict:
    approved: bool
    violated_rules: list[str] = field(default_factory=list)


def _violations_for_message_action(
    case: Case,
    decision: Decision,
    policy: PolicySnapshot,
    ctx: GateContext,
    violations: list[str],
) -> None:
    params = decision.action_params
    channel = str(params.get("channel", ""))

    if channel not in policy.allowed_channels:
        violations.append(f"channel_not_allowed:{channel}")
    if channel in policy.consent_required_channels:
        opted_in = ctx.customer_sms_opt_in if channel == "sms" else ctx.customer_whatsapp_opt_in
        if not opted_in:
            violations.append(f"consent_missing:{channel}")
    if channel in IDENTITY_VERIFIED_CHANNELS and channel not in ctx.verified_channels:
        violations.append(f"sender_unverified:{channel}")

    if in_quiet_hours(ctx.now, policy, ctx.tz):
        violations.append("quiet_hours")

    window_start = ctx.now - timedelta(days=policy.message_cap_window_days)
    recent = sum(1 for ts in ctx.messages_sent_at if ts > window_start)
    if recent >= policy.message_cap_per_case:
        violations.append("message_cap_exceeded")

    # Link trust requirements — §2.6 / spec §5.7.
    if int(params.get("expire_hours", -1)) != policy.payment_link_expiry_hours:
        violations.append("link_expiry_invalid")
    if bool(params.get("single_use", False)) != policy.payment_link_single_use:
        violations.append("link_not_single_use")
    domain = str(params.get("hosted_domain", "")).lower()
    if domain not in TRUSTED_LINK_DOMAINS:
        violations.append("link_domain_untrusted")
    if not str(params.get("order_ref", "")).strip():
        violations.append("order_reference_missing")

    body = str(params.get("message_body", ""))
    if has_sensitive_content(body):
        violations.append("sensitive_content_in_message")


def check_guardrails(
    case: Case,
    decision: Decision,
    policy_snapshot: PolicySnapshot,
    ctx: GateContext,
) -> GuardrailVerdict:
    violations: list[str] = []

    action = decision.proposed_action
    EXECUTING_ACTIONS = {"send_payment_link", "retry_mandate_charge"}

    # --- rules binding only revenue-affecting actions ---------------------------
    # escalate_human / stop are deliberately exempt: escalation must remain
    # possible precisely when retries are exhausted or the TTL has lapsed.
    if action in EXECUTING_ACTIONS:
        if case.state == CaseState.AWAITING_OUTCOME:
            violations.append("case_not_actionable")

        if case.attempts_count >= policy_snapshot.max_retries_per_case:
            violations.append("retry_cap_exceeded")

        if case.case_deadline_at is not None and ctx.now >= case.case_deadline_at:
            violations.append("case_ttl_expired")

        cooldown = timedelta(hours=policy_snapshot.retry_cooldown_hours)
        if case.last_action_at is not None and ctx.now - case.last_action_at < cooldown:
            violations.append("cooldown_active")

        if policy_snapshot.amount_immutability:
            proposed_amount = decision.action_params.get("amount")
            if proposed_amount is not None and int(proposed_amount) != case.amount:
                violations.append("amount_mutation")

    # --- per-action rules --------------------------------------------------------
    if action == "send_payment_link":
        if case.flow_type != FlowType.PAYMENT_FAILURE and case.flow_type != FlowType.CHECKOUT_ABANDONMENT:
            violations.append("action_flow_mismatch")
        _violations_for_message_action(case, decision, policy_snapshot, ctx, violations)

    elif action == "retry_mandate_charge":
        if case.flow_type != FlowType.SUBSCRIPTION_MANDATE:
            violations.append("action_flow_mismatch")
        # Deliberately NOT subject to quiet hours or message caps: a mandate retry
        # is agent-side against stored authorization, not a customer-facing message.

    elif action == "escalate_human":
        if not str(decision.action_params.get("reason", "")).strip():
            violations.append("escalation_reason_missing")

    elif action == "stop":
        pass  # stopping is always permitted

    else:
        violations.append("unknown_action")

    return GuardrailVerdict(approved=not violations, violated_rules=violations)
