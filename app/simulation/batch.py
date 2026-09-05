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

from app.core.clock import as_naive_utc
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
# because probability compounds across several observation wakes. The
# unrecoverable leak is kept small: customers who were going to pay anyway do
# so quickly, and a large per-wake leak would compound across a 14-day TTL
# until "unrecoverable" lost its meaning.
PAY_PROB_RECOVERABLE = 0.40
PAY_PROB_UNRECOVERABLE = 0.02
# Attention decay while HOLDING after the retry cap: an outstanding link still
# converts, but at a fading rate the longer the customer ignores it.
_HOLD_DECAY = 0.15
# Replay history length: two TTL windows. Recoveries can land at any age, but a
# LOST resolution needs the full case TTL (14 days) to elapse — a shorter window
# starves the loss side and silently inflates the recovery rate. Cases inside
# the newest TTL stay open, exactly as a live console would show them.
_TIMELINE_DAYS = 28


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
    now: datetime | None = None,
) -> dict:
    """Replay `count` synthetic failures through the real pipeline.

    The replay never fabricates the future: observation wakes, recoveries, and
    TTL closures are only recorded up to `now` (the wall clock unless overridden).
    Cases whose story hasn't finished by then stay AWAITING_OUTCOME — exactly as
    a live console would show them — and the real maintenance scheduler finishes
    them. Pass `now` explicitly for fully deterministic reports.
    """
    rng = random.Random(seed)
    real_now = as_naive_utc(now) if now is not None else datetime.now().astimezone().replace(tzinfo=None)
    run_row = SimulationRun(seed=seed, cases_requested=count)
    session.add(run_row)
    session.flush()

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    ensure_default_merchant(session)
    generator = SyntheticEventGenerator(seed=seed)

    # Timeline spans the twenty days ending now, so the batch reads as history:
    # older cases have fully resolved, recent ones are still open.
    start_at = real_now - timedelta(days=_TIMELINE_DAYS)
    failures = generator.generate_batch(count=count, start_at=start_at, span_hours=_TIMELINE_DAYS * 24)
    wall_start = datetime.now()

    cases: list[tuple[Case, SyntheticFailure]] = []
    for i, failure in enumerate(failures):
        event = _ingest(session, failure)
        case = create_case_for_event(session, event, now=failure.occurred_at, simulation_run_id=run_row.id)
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
                if case.deferred_until > real_now:
                    break  # the retry wake itself hasn't happened yet — stays open
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
                    break  # payment would land past TTL → the TTL sweep closes it
                if recovered_at > real_now:
                    break  # payment hasn't actually landed yet — the case stays open
                record_recovery(
                    session, case,
                    payment_id=f"pay_{rng.randrange(16**10):010x}",
                    amount=case.amount,
                    recovered_at=recovered_at,
                )
                break

            next_wake = sim_now + timedelta(hours=cooldown_hours)
            if case.case_deadline_at is not None and next_wake >= case.case_deadline_at:
                if case.case_deadline_at <= real_now:
                    mark_lost(session, case, now=case.case_deadline_at)
                break

            if next_wake > real_now:
                break  # next observation wake is in the future — nothing recorded yet

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
                        if case.case_deadline_at <= real_now:
                            mark_lost(session, case, now=case.case_deadline_at)
                        break
                    if sim_now > real_now:
                        break  # past the wall clock — the link is still live
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

        # TTL sweep only for deadlines that have genuinely elapsed; cases still
        # inside their observation window remain open for the real scheduler.
        if (
            case.state == CaseState.AWAITING_OUTCOME
            and case.case_deadline_at is not None
            and case.case_deadline_at <= real_now
        ):
            mark_lost(session, case, now=case.case_deadline_at)
        session.commit()

    summary = compute_summary(session, now=datetime.now().astimezone(), simulation_run_id=run_row.id)

    # Settled cohort — cases whose full observation window (TTL) has elapsed, so
    # every one of them has had its complete chance to recover or be lost. The
    # global rate is censored: recoveries resolve at any age while losses need a
    # full TTL, so a bounded history structurally over-weights the loss-free
    # recent tail. The settled rate is the censoring-free number.
    settled = [
        c for c, _ in cases
        if c.case_deadline_at is not None and c.case_deadline_at <= real_now
    ]
    settled_recovered = sum(1 for c in settled if c.state == CaseState.RECOVERED)
    settled_lost = sum(1 for c in settled if c.state == CaseState.LOST)
    settled_denom = settled_recovered + settled_lost
    summary["settled_cohort"] = {
        "cases": len(settled),
        "recovered": settled_recovered,
        "lost": settled_lost,
        "escalated": sum(1 for c in settled if c.state == CaseState.ESCALATED_TO_HUMAN),
        "stopped": sum(1 for c in settled if c.state == CaseState.STOPPED_UNRECOVERABLE),
        "recovery_rate": round(settled_recovered / settled_denom, 4) if settled_denom else 0.0,
    }

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
        f"settled-cohort rate {summary['settled_cohort']['recovery_rate']:.1%} "
        f"({summary['settled_cohort']['cases']} settled) · "
        f"guardrail violations: {summary['guardrail_violations']}"
    )
    logger.info(
        "batch %s finished: %.1f%% recovery rate (settled %.1f%%)",
        run_row.id,
        summary["recovery_rate"] * 100,
        summary["settled_cohort"]["recovery_rate"] * 100,
    )
    return summary


_ = (Event,)  # re-exported typing parity
