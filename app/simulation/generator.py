"""Synthetic event generator — Faker-based, labeled with ground truth.

Each generated failure carries `ground_truth_recoverable`: whether a well-timed
recovery action would plausibly have brought the money back. The mix targets
35–55% recoverable — most failures are genuinely unrecoverable, so any batch
report near 90% recovery means something is broken, not impressive.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from faker import Faker

from app.models.entities import EventType

# (error_code, description) pairs grouped by ground truth.
RECOVERABLE_FAILURES = [
    ("gateway_timeout", "Payment timed out at bank end"),
    ("transaction_processing_error", "No response from bank gateway"),
    ("authentication_failure", "3DS authentication page timed out"),
    ("upi_collect_expired", "UPI collect request expired before approval"),
]
UNRECOVERABLE_FAILURES = [
    ("insufficient_funds", "Customer has insufficient balance"),
    ("card_expired", "Card has expired"),
    ("customer_cancelled", "Payment cancelled by customer"),
    ("mandate_revoked", "Mandate revoked by customer at bank"),
    ("bank_declined", "Issuer bank declined the transaction"),
]

SUBSCRIPTION_RECOVERABLE = [
    ("gateway_timeout", "Recurring charge timed out at bank"),
    ("transaction_processing_error", "NACH processing error, retry viable"),
]
SUBSCRIPTION_UNRECOVERABLE = [
    ("insufficient_funds", "Insufficient balance for mandate debit"),
    ("mandate_revoked", "e-NACH mandate revoked by customer"),
]

# A slice of real traffic defies classification: opaque processor errors with no
# usable signal. These stay unknown → the agent escalates instead of guessing.
OPAQUE_FAILURES = [
    ("internal_error", "Unexpected processor response"),
    (None, None),
]


@dataclass(frozen=True)
class SyntheticFailure:
    source_event_id: str
    event_type: EventType
    amount: int                    # paise
    order_id: str | None
    subscription_id: str | None
    error_code: str | None
    error_description: str | None
    occurred_at: datetime
    ground_truth_recoverable: bool


class SyntheticEventGenerator:
    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._faker = Faker("en_IN")
        if seed is not None:
            Faker.seed(seed)
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._rng.randrange(16**8):08x}{self._seq}"

    def _amount(self) -> int:
        return self._rng.choice([299_00, 499_00, 1_200_00, 2_450_00, 3_999_00, 5_500_00])

    def _pick(self, table: list[tuple[str, str]]) -> tuple[str, str]:
        return self._rng.choice(table)

    def generate_batch(
        self,
        *,
        count: int,
        start_at: datetime,
        span_hours: float = 72.0,
        recoverable_fraction: float = 0.45,
        flow_weights: tuple[float, float, float] = (0.55, 0.25, 0.20),
    ) -> list[SyntheticFailure]:
        """`count` labeled failures spread over a synthetic timeline."""
        out: list[SyntheticFailure] = []
        for _ in range(count):
            roll = self._rng.random()
            cumulative = 0.0
            flow = "A"
            for label, weight in zip(("A", "B", "C"), flow_weights, strict=False):
                cumulative += weight
                if roll <= cumulative:
                    flow = label
                    break

            recoverable = self._rng.random() < recoverable_fraction
            occurred = start_at + timedelta(
                hours=self._rng.uniform(0, span_hours),
                minutes=self._rng.randrange(60),
            )

            if flow == "C":
                code, desc = self._pick(SUBSCRIPTION_RECOVERABLE if recoverable else SUBSCRIPTION_UNRECOVERABLE)
                out.append(SyntheticFailure(
                    source_event_id=self._next_id("evt_sub"),
                    event_type=EventType.SUBSCRIPTION_CHARGE_FAILED,
                    amount=self._amount(),
                    order_id=None,
                    subscription_id=self._next_id("sub"),
                    error_code=code,
                    error_description=desc,
                    occurred_at=occurred,
                    ground_truth_recoverable=recoverable,
                ))
                continue

            if flow == "B":
                # Abandonment is inherently customer-completable → mostly recoverable.
                recoverable = self._rng.random() < max(recoverable_fraction, 0.6)
                out.append(SyntheticFailure(
                    source_event_id=self._next_id("evt_co"),
                    event_type=EventType.CHECKOUT_ABANDONED,
                    amount=self._amount(),
                    order_id=self._next_id("order"),
                    subscription_id=None,
                    error_code="checkout_abandoned",
                    error_description="Checkout created but not completed within timeout window",
                    occurred_at=occurred,
                    ground_truth_recoverable=recoverable,
                ))
                continue

            code, desc = self._pick(RECOVERABLE_FAILURES if recoverable else UNRECOVERABLE_FAILURES)
            if self._rng.random() < 0.08:
                # Real traffic includes unclassifiable failures; keep the agent honest.
                code, desc = self._pick(OPAQUE_FAILURES)
            out.append(SyntheticFailure(
                source_event_id=self._next_id("evt_pay"),
                event_type=EventType.PAYMENT_FAILED,
                amount=self._amount(),
                order_id=self._next_id("order"),
                subscription_id=None,
                error_code=code,
                error_description=desc,
                occurred_at=occurred,
                ground_truth_recoverable=recoverable,
            ))
        out.sort(key=lambda s: s.occurred_at)
        return out
