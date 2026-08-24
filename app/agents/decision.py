"""Decision agent — proposes exactly one bounded recovery action per case.

This agent has NO execution capability. It returns data; the guardrail gate then
approves or blocks, and only the execution layer acts.

Structure:
1. A deterministic template layer computes the *candidate* action set from the
   policy snapshot, case history, channel consent/verification state, and the
   diagnosis — this always runs, with or without an LLM.
2. When the LLM is enabled AND the diagnosis is confident enough, Claude chooses
   among the candidates and picks message language/tone (including Hinglish).
   Its answer is validated against the candidate set; anything invalid falls
   back to the template choice.
3. Without the LLM, the template choice is used directly (LLM_DISABLED mode).

Low-confidence diagnoses are treated conservatively: escalate rather than message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field

from app.agents.diagnosis import DiagnosisResult
from app.agents.llm import LLMError, LLMTimedOut, get_llm
from app.core.config import Settings, get_settings
from app.guardrails.checks import GateContext
from app.guardrails.policy import PolicySnapshot
from app.models.entities import Case, FlowType

logger = logging.getLogger(__name__)

CONFIDENCE_FLOOR = 0.5

CHANNEL_ORDER = ("whatsapp", "sms", "email")  # richest → most transactional


@dataclass(frozen=True)
class DecisionProposal:
    proposed_action: str  # send_payment_link | retry_mandate_charge | escalate_human | stop
    action_params: dict = field(default_factory=dict)
    message_language: str | None = None   # en | hi | hinglish
    message_tone: str | None = None       # formal | friendly | urgent
    reasoning: str = ""
    decided_by: str = "template"          # "llm" | "template"


class DecisionOutput(BaseModel):
    """Structured schema for the LLM path."""

    chosen_index: int = Field(ge=0, description="index into the provided candidate list")
    message_language: str = Field(description="en, hi, or hinglish")
    message_tone: str = Field(description="formal, friendly, or urgent")
    reasoning: str


_DECISION_SYSTEM = (
    "You are the decision step of a payment-recovery system for Indian merchants. "
    "You choose ONE recovery action from a fixed candidate list — you cannot invent "
    "actions, change amounts, or execute anything. Prefer gentle customer-completed "
    "recovery over escalation when a compliant channel exists."
)


def _available_channels(case: Case, policy: PolicySnapshot, ctx: GateContext) -> list[str]:
    usable: list[str] = []
    for channel in CHANNEL_ORDER:
        if channel not in policy.allowed_channels:
            continue
        if channel in policy.consent_required_channels:
            opted_in = ctx.customer_sms_opt_in if channel == "sms" else ctx.customer_whatsapp_opt_in
            if not opted_in:
                continue
        if channel == "sms" and "sms" not in ctx.verified_channels:
            continue
        if channel == "whatsapp" and "whatsapp" not in ctx.verified_channels:
            continue
        usable.append(channel)
    return usable


def _template_candidates(
    case: Case,
    policy: PolicySnapshot,
    ctx: GateContext,
    diagnosis: DiagnosisResult,
) -> tuple[list[tuple[str, dict]], str]:
    """(candidates, template_reason). Ordered best-first."""
    attempts_left = case.attempts_count < policy.max_retries_per_case
    ttl_ok = case.case_deadline_at is None or ctx.now < case.case_deadline_at

    if not ttl_ok:
        return (
            [("stop", {"reason": f"case TTL of {policy.case_ttl_days} days elapsed"})],
            f"case past {policy.case_ttl_days}-day TTL",
        )

    if not attempts_left:
        return (
            [
                ("escalate_human", {"reason": "retry cap reached without recovery", "queue": "ops-review"}),
                ("stop", {"reason": "retry cap reached without recovery"}),
            ],
            "retries exhausted",
        )

    if diagnosis.confidence < CONFIDENCE_FLOOR or diagnosis.root_cause_category == "unknown":
        reason = f"low-confidence diagnosis ({diagnosis.root_cause_category}); human review before messaging"
        return (
            [("escalate_human", {"reason": reason, "queue": "ops-review"})],
            "diagnosis below confidence floor",
        )

    if diagnosis.root_cause_category == "customer_cancelled":
        # The customer explicitly cancelled. Messaging them anyway is spam with
        # a compliance tail — decline to act rather than chase.
        return (
            [("stop", {"reason": "customer explicitly cancelled the payment — no further messaging"})],
            "customer-initiated cancellation — respect it, do not message",
        )

    if case.flow_type == FlowType.SUBSCRIPTION_MANDATE:
        if diagnosis.root_cause_category == "mandate_revoked":
            return (
                [
                    ("send_payment_link", {"note": "renew-mandate link"}),
                    ("escalate_human",
                     {"reason": "mandate revoked; renewal needs customer action", "queue": "ops-review"}),
                ],
                "mandate revoked — customer must re-authorise",
            )
        viable = f"mandate retry viable after '{diagnosis.root_cause_category}'"
        return ([("retry_mandate_charge", {})], viable)

    channels = _available_channels(case, policy, ctx)
    if not channels:
        fallback_action = (
            "retry_mandate_charge" if case.flow_type == FlowType.SUBSCRIPTION_MANDATE else "escalate_human"
        )
        params = (
            {"reason": "no consented verified channel available for messaging", "queue": "ops-review"}
            if fallback_action == "escalate_human"
            else {}
        )
        return ([(fallback_action, params)], "no consented verified channel")

    candidates = [(f"send_payment_link:{channel}", {"channel": channel}) for channel in channels]
    candidates.append(("stop", {"reason": "no recovery attempt judged worthwhile"}))
    return candidates, f"consented verified channels available: {', '.join(channels)}"


def decide(
    case: Case,
    policy: PolicySnapshot,
    ctx: GateContext,
    diagnosis: DiagnosisResult,
    *,
    now: datetime,
    settings: Settings | None = None,
) -> DecisionProposal:
    settings = settings or get_settings()
    candidates, template_reason = _template_candidates(case, policy, ctx, diagnosis)

    template_choice = DecisionProposal(
        proposed_action=candidates[0][0],
        action_params=dict(candidates[0][1]),
        reasoning=template_reason[:280],
        decided_by="template",
    )

    llm = get_llm()
    if not llm.available or diagnosis.method == "fallback" or len(candidates) <= 1:
        return template_choice

    listing = "\n".join(f"{i}: {name} {params}" for i, (name, params) in enumerate(candidates))
    prompt = (
        f"Case flow: {case.flow_type.value}\n"
        f"Attempt number: {case.attempts_count + 1} of {policy.max_retries_per_case}\n"
        f"Diagnosis: {diagnosis.root_cause_category} (confidence {diagnosis.confidence:.2f})\n"
        f"Customer locale preference: en\n"
        f"Candidate actions:\n{listing}\n"
        "Choose one candidate and pick language/tone for the customer message "
        "(Hinglish is often effective for casual consumer purchases in India)."
    )
    try:
        out = llm.classify(system=_DECISION_SYSTEM, prompt=prompt, schema=DecisionOutput)
    except (LLMTimedOut, LLMError) as exc:
        logger.warning("decision LLM unavailable (%s); using template choice", exc)
        return template_choice

    idx = out.chosen_index
    if idx < 0 or idx >= len(candidates):
        logger.warning("decision LLM returned out-of-range index %s; using template choice", idx)
        return template_choice

    action_name, params = candidates[idx]
    language = out.message_language if out.message_language in ("en", "hi", "hinglish") else "en"
    tone = out.message_tone if out.message_tone in ("formal", "friendly", "urgent") else "friendly"
    return DecisionProposal(
        proposed_action=action_name,
        action_params=dict(params),
        message_language=language,
        message_tone=tone,
        reasoning=out.reasoning[:280] or template_reason[:280],
        decided_by="llm",
    )
