"""Batch simulation runner — drives the real pipeline over a synthetic timeline.

The pipeline itself never reads the wall clock (`now` is always a parameter), so
a 14-day case TTL and 6-hour cooldowns elapse instantly in simulated time while
exercising byte-for-byte the same code paths as live operation.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlmodel import Session

from app.models.entities import (
    Case,
    CaseState,
    Event,
    SimulationRun,
)
from app.services.metrics import compute_summary
from app.services.pipeline import (
    create_case_for_event,
    ensure_default_merchant,
    mark_lost,
    process_case,
    record_recovery,
)
from app.simulation.generator import SyntheticEventGenerator, SyntheticFailure

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

_MAX_LOOP_GUARD = 8

# Outcome-model calibration: a healthy batch should land in a believable
# 35–55% recovery band. Per-wake conversion is well below the headline rate
# because probability compounds across several observation wakes.
PAY_PROB_RECOVERABLE = 0.40
PAY_PROB_UNRECOVERABLE = 0.04
# Attention decay while HOLDING after the retry cap: an outstanding link still
# converts, but at a fading rate the longer the customer ignores it.
_HOLD_DECAY = 0.15


def _ingest(session: Session, failure: SyntheticFailure) -> Event:
    event = Event(
        source_event_id=failure.source_event_id,
        type=failure.event_type,
        razorpay_payload_ref=failure.source_event_id,
        amount=failure.amount,
        currency="INR",
        order_id=failure.order_id,
        subscription_id=failure.subscription_id,
        error_code=failure.error_code,
        error_description=failure.error_description,
        payload={"synthetic": True},
        ground_truth_recoverable=failure.ground_truth_recoverable,
        occurred_at=failure.occurred_at,
    )
    session.add(event)
    session.flush()
    return event


def _policy_int(policy: dict, key: str, default: int) -> int:
    try:
        value = policy.get(key, default)
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def run_batch(
    session: Session,
    *,
    count: int = 200,
    seed: int | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    rng = random.Random(seed)
    run_row = SimulationRun(seed=seed, cases_requested=count)
    session.add(run_row)
    session.flush()

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    ensure_default_merchant(session)
    generator = SyntheticEventGenerator(seed=seed)

    # Timeline starts "now" minus a day so all cases are already live.
    start_at = datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=1)
    failures = generator.generate_batch(count=count, start_at=start_at)
    wall_start = datetime.now()

    cases: list[tuple[Case, SyntheticFailure]] = []
    for i, failure in enumerate(failures):
        event = _ingest(session, failure)
        case = create_case_for_event(session, event, now=failure.occurred_at)
        process_case(session, case, now=failure.occurred_at)
        cases.append((case, failure))
        if i % 25 == 0:
            session.commit()
            emit(f"processed {i + 1}/{len(failures)} cases…")
    session.commit()
    run_row.cases_created = len(cases)

    # ── outcome observation loop — simulated scheduler wake-ups ──────────────
    emit("observing outcomes on the synthetic timeline…")
    for case, failure in sorted(cases, key=lambda pair: pair[0].created_at):
        policy = case.policy_snapshot
        max_retries = _policy_int(policy, "max_retries_per_case", 3)
        message_cap = _policy_int(policy, "message_cap_per_case", 2)
        cooldown_hours = float(_policy_int(policy, "retry_cooldown_hours", 6))
        pay_prob = PAY_PROB_RECOVERABLE if failure.ground_truth_recoverable else PAY_PROB_UNRECOVERABLE
        sim_now = case.last_action_at or case.created_at
        loop_guard = 0

        while case.state == CaseState.AWAITING_OUTCOME and loop_guard < _MAX_LOOP_GUARD:
            loop_guard += 1
            if case.deferred_until is not None and case.attempts_count == 0:
                # Timing-blocked before ever reaching the customer — jump to the
                # retry window first; no payment can arrive for a link never sent.
                sim_now = max(sim_now, case.deferred_until)
                attempt = case.attempts_count + 1
                process_case(
                    session, case, now=sim_now,
                    latest_failure_reason=f"retry attempt {attempt} failed: {failure.error_code} recurred",
                )
                continue

            if rng.random() < pay_prob:
                recovered_at = sim_now + timedelta(minutes=rng.randrange(20, 60 * 18))
                if case.case_deadline_at is not None and recovered_at >= case.case_deadline_at:
                    break  # payment would land past TTL → stays open; TTL sweep closes it
                record_recovery(
                    session, case,
                    payment_id=f"pay_{rng.randrange(16**10):010x}",
                    amount=case.amount,
                    recovered_at=recovered_at,
                )
                break

            next_wake = sim_now + timedelta(hours=cooldown_hours)
            if case.case_deadline_at is not None and next_wake >= case.case_deadline_at:
                mark_lost(session, case, now=case.case_deadline_at)
                break

            if case.attempts_count >= max_retries or case.messages_sent_count >= message_cap:
                # Policy exhausted (retry cap and/or message cap) but the last
                # link/charge is still live — hold and keep observing until TTL
                # rather than manufacture a proposal the gate must refuse, or
                # force a human review for routine non-response. Compressed
                # wake-ups with decaying attention model late conversions.
                sim_now = next_wake
                while case.state == CaseState.AWAITING_OUTCOME:
                    sim_now += timedelta(hours=12)
                    if case.case_deadline_at is not None and sim_now >= case.case_deadline_at:
                        mark_lost(session, case, now=case.case_deadline_at)
                        break
                    if rng.random() < pay_prob * _HOLD_DECAY:
                        record_recovery(
                            session, case,
                            payment_id=f"pay_{rng.randrange(16**10):010x}",
                            amount=case.amount,
                            recovered_at=sim_now,
                        )
                        break
                break

            # Cooldown elapsed on the simulated clock → retry loop-back.
            sim_now = next_wake
            new_reason = f"retry attempt {case.attempts_count + 1} failed: {failure.error_code} recurred"
            process_case(session, case, now=sim_now, latest_failure_reason=new_reason)

        if case.state == CaseState.AWAITING_OUTCOME and case.case_deadline_at is not None:
            mark_lost(session, case, now=case.case_deadline_at)
        session.commit()

    summary = compute_summary(session, now=datetime.now().astimezone())
    summary["ground_truth"] = {
        "labeled_cases": len(cases),
        "recoverable_labeled": sum(1 for _, f in cases if f.ground_truth_recoverable),
        "recoverable_share": round(
            sum(1 for _, f in cases if f.ground_truth_recoverable) / max(len(cases), 1), 3
        ),
    }
    summary["wall_clock_seconds"] = round((datetime.now() - wall_start).total_seconds(), 2)

    run_row.report = summary
    run_row.finished_at = datetime.now()
    session.add(run_row)
    session.commit()

    emit(
        f"batch complete — {summary['cases_total']} cases · "
        f"{summary['recovered']} recovered ({summary['recovery_rate']:.1%}) · "
        f"guardrail violations: {summary['guardrail_violations']}"
    )
    logger.info("batch %s finished: %.1f%% recovery rate", run_row.id, summary["recovery_rate"] * 100)
    return summary


_ = (Event,)  # re-exported typing parity
