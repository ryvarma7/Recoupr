"""Diagnosis agent — rule layer first, Claude for the ambiguous remainder.

A deterministic rule layer intercepts any event carrying a known Razorpay error
code or a recognisable error description (no LLM call, method="rule"). Everything
else goes to claude-sonnet-5 with structured output (method="llm"). When the LLM
is disabled and no rule matches, a conservative heuristic classifies as unknown
(method="fallback") and the decision layer treats low confidence accordingly.

`method` is recorded on every row so the rule-vs-model split is auditable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pydantic import BaseModel, Field

from app.agents.llm import LLMError, LLMTimedOut, get_llm
from app.models.entities import Event, EventType

logger = logging.getLogger(__name__)

MAX_REASONING_CHARS = 280


@dataclass(frozen=True)
class DiagnosisResult:
    root_cause_category: str
    confidence: float
    method: str  # "rule" | "llm" | "fallback"
    reasoning: str


class DiagnosisUnavailable(RuntimeError):
    """Raised when diagnosis cannot complete safely — pipeline escalates, never fails open."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# Known Razorpay-shaped failure codes → deterministic classification.
RULE_TABLE: dict[str, tuple[str, float]] = {
    "insufficient_funds": ("insufficient_funds", 0.98),
    "card_declined_insufficient_funds": ("insufficient_funds", 0.98),
    "authentication_failure": ("auth_3ds_failure", 0.95),
    "payment_authentication_failed": ("auth_3ds_failure", 0.95),
    "card_expired": ("expired_card", 0.97),
    "gateway_timeout": ("bank_timeout", 0.90),
    "bank_timeout": ("bank_timeout", 0.92),
    "transaction_processing_error": ("bank_timeout", 0.85),
    "bank_declined": ("bank_declined", 0.85),
    "mandate_revoked": ("mandate_revoked", 0.98),
    "nach_mandate_revoked": ("mandate_revoked", 0.98),
    "upi_collect_expired": ("upi_request_expired", 0.93),
    "customer_cancelled": ("customer_cancelled", 0.95),
    "payment_cancelled_by_customer": ("customer_cancelled", 0.95),
}

# Keyword fallbacks over the description text (still fully deterministic).
KEYWORD_RULES: list[tuple[str, tuple[str, ...], float]] = [
    ("insufficient_funds", ("insufficient funds", "not enough balance", "low balance"), 0.9),
    ("auth_3ds_failure", ("authentication", "3ds", "otp entry failed"), 0.88),
    ("expired_card", ("expired card", "card expired"), 0.9),
    ("bank_timeout", ("timed out", "timeout at bank", "no response from bank"), 0.87),
    ("mandate_revoked", ("mandate revoked", "mandate cancelled"), 0.94),
]


class DiagnosisOutput(BaseModel):
    """Structured schema for the LLM path."""

    root_cause_category: str = Field(
        description=(
            "One of: insufficient_funds, auth_3ds_failure, expired_card, bank_timeout, "
            "bank_declined, mandate_revoked, upi_request_expired, customer_cancelled, "
            "checkout_abandoned, price_hesitation, technical_drop, unknown"
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=280)


_DIAGNOSIS_SYSTEM = (
    "You classify why an Indian digital payment failed, for a payments-operations system. "
    "Answer with the structured output only. Be conservative: if the signals don't clearly "
    "indicate a category, use 'unknown' with confidence <= 0.4."
)


def _rule_classify(event: Event) -> DiagnosisResult | None:
    code = (event.error_code or "").strip().lower()
    if code in RULE_TABLE:
        category, confidence = RULE_TABLE[code]
        return DiagnosisResult(
            root_cause_category=category,
            confidence=confidence,
            method="rule",
            reasoning=f"matched known error code '{event.error_code}'",
        )
    description = (event.error_description or "").lower()
    for category, keywords, confidence in KEYWORD_RULES:
        if any(keyword in description for keyword in keywords):
            return DiagnosisResult(
                root_cause_category=category,
                confidence=confidence,
                method="rule",
                reasoning=f"description matched keyword for '{category}'",
            )
    return None


def diagnose(event: Event, *, latest_failure_reason: str | None = None) -> DiagnosisResult:
    """Classify one failure event. Raises DiagnosisUnavailable only on LLM errors."""
    if event.type == EventType.CHECKOUT_ABANDONED:
        # An abandonment is not a failure with an error code — the customer
        # simply never completed payment. Deterministic, no LLM needed.
        return DiagnosisResult(
            root_cause_category="checkout_abandoned",
            confidence=0.92,
            method="rule",
            reasoning="no payment attempted — checkout abandoned before completion",
        )

    ruled = _rule_classify(event)
    if ruled is not None:
        return ruled

    llm = get_llm()
    if not llm.available:
        return fallback_diagnose()

    prompt = (
        f"Event type: {event.type.value}\n"
        f"Amount (paise): {event.amount}\n"
        f"Error code: {event.error_code or '(none provided)'}\n"
        f"Error description: {event.error_description or '(none provided)'}\n"
        + (f"Most recent retry failure reason: {latest_failure_reason}\n" if latest_failure_reason else "")
        + "Why did this payment fail? Classify the root cause."
    )
    try:
        out = llm.classify(system=_DIAGNOSIS_SYSTEM, prompt=prompt, schema=DiagnosisOutput)
    except (LLMTimedOut, LLMError) as exc:
        raise DiagnosisUnavailable(str(exc)) from exc

    return DiagnosisResult(
        root_cause_category=out.root_cause_category,
        confidence=float(min(max(out.confidence, 0.0), 1.0)),
        method="llm",
        reasoning=out.reasoning[:MAX_REASONING_CHARS],
    )


def fallback_diagnose() -> DiagnosisResult:
    """LLM disabled and no rule matched — conservative unknown classification."""
    return DiagnosisResult(
        root_cause_category="unknown",
        confidence=0.3,
        method="fallback",
        reasoning="no known error code and LLM disabled; classified conservatively as unknown",
    )
