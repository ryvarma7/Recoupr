"""Success-metrics computation — spec §2.9.

Definitions held honestly:
- Recovery rate = recovered ÷ (recovered + lost). Cases still open are PENDING
  and never folded into either side.
- A "guardrail violation" is an executed action lacking a passing GuardrailCheck
  row — an invariant breach that must be zero. Blocked proposals are NOT
  violations; they're the gate doing its job, reported separately.
- False-positive rate = share of acted-on labeled cases whose ground truth said
  unrecoverable (computed only over synthetic batches carrying labels).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from app.models.entities import (
    Action,
    Case,
    CaseState,
    Decision,
    DecisionStatus,
    Diagnosis,
    Event,
    GuardrailCheck,
    Outcome,
    OutcomeType,
)

OPEN_STATES = {CaseState.NEW, CaseState.PROCESSING, CaseState.AWAITING_OUTCOME}


def compute_summary(session: Session, *, now: datetime | None = None, simulation_run_id: int | None = None) -> dict:
    cases = session.exec(select(Case)).all()
    if simulation_run_id is not None:
        cases = [c for c in cases if c.simulation_run_id == simulation_run_id]
    case_ids = {c.id for c in cases}
    now = now or datetime.now().astimezone()

    def count(state_set: set[CaseState]) -> int:
        return sum(1 for c in cases if c.state in state_set)

    recovered = [c for c in cases if c.state == CaseState.RECOVERED]
    lost = [c for c in cases if c.state == CaseState.LOST]
    escalated = [c for c in cases if c.state == CaseState.ESCALATED_TO_HUMAN]
    stopped = [c for c in cases if c.state == CaseState.STOPPED_UNRECOVERABLE]
    pending = [c for c in cases if c.state in OPEN_STATES]

    outcomes = [o for o in session.exec(select(Outcome)).all() if o.case_id in case_ids]
    total_recovered_paise = sum(o.amount_recovered for o in outcomes if not o.late_recovery_after_ttl)

    denominator = len(recovered) + len(lost)
    recovery_rate = (len(recovered) / denominator) if denominator else 0.0

    tt_recovery_hours = [
        (o.recovered_at - c.created_at).total_seconds() / 3600.0
        for o in outcomes
        if o.outcome_type == OutcomeType.RECOVERED and o.recovered_at is not None
        and not o.late_recovery_after_ttl
        for c in cases
        if c.id == o.case_id
    ]
    mean_ttr = sum(tt_recovery_hours) / len(tt_recovery_hours) if tt_recovery_hours else 0.0

    # Resolution split — how money actually came back / where human effort went.
    flow_c_resolved = sum(1 for c in recovered if c.flow_type.value == "C")
    ab_link_resolved = sum(1 for c in recovered if c.flow_type.value in ("A", "B"))
    resolved_total = max(len(recovered), 1)

    # Ground-truth false positives (synthetic batches only). A case counts as
    # acted-on when the agent actually reached toward the customer — messaging or
    # charging. Escalations (no customer contact) and stops (deliberate decline)
    # are excluded: declining to act can't be a false positive.
    events = {e.id: e for e in session.exec(select(Event)).all() if e.id in {c.event_id for c in cases}}
    acted_case_ids = {
        a.case_id for a in session.exec(select(Action)).all() if a.case_id in case_ids
        if a.action_type in ("send_payment_link", "retry_mandate_charge")
    }
    labeled_acted = [
        events[c.event_id] for c in cases
        if c.id in acted_case_ids and events.get(c.event_id) is not None
        and events[c.event_id].ground_truth_recoverable is not None
    ]
    fp_rate = 0.0
    if labeled_acted:
        fps = sum(1 for e in labeled_acted if not e.ground_truth_recoverable)
        fp_rate = fps / len(labeled_acted)

    checks = [c for c in session.exec(select(GuardrailCheck)).all() if c.case_id in case_ids]
    decisions = {d.id: d for d in session.exec(select(Decision)).all() if d.case_id in case_ids}
    diagnoses = [d for d in session.exec(select(Diagnosis)).all() if d.case_id in case_ids]

    real_violations = 0
    for check in checks:
        decision = decisions.get(check.decision_id)
        if (
            decision is not None
            and decision.status == DecisionStatus.EXECUTED
            and decision.proposed_action != "escalate_human"
            and not check.passed
        ):
            real_violations += 1

    return {
        "cases_total": len(cases),
        "recovered": len(recovered),
        "lost": len(lost),
        "escalated": len(escalated),
        "stopped_unrecoverable": len(stopped),
        "pending": len(pending),
        "total_recovered_paise": total_recovered_paise,
        "recovery_rate": round(recovery_rate, 4),
        "mean_time_to_recovery_hours": round(mean_ttr, 2),
        "resolved_via_mandate_retry_pct": round(100 * flow_c_resolved / resolved_total, 1),
        "resolved_via_payment_link_pct": round(100 * ab_link_resolved / resolved_total, 1),
        "escalated_pct": round(100 * len(escalated) / max(len(cases), 1), 1),
        "false_positive_rate": round(fp_rate, 4),
        "guardrail_violations": real_violations,          # must be zero on a clean batch
        "guardrail_blocks": sum(1 for c in checks if not c.passed),  # the gate doing its job
        "at_risk_paise": sum(c.amount for c in cases if c.state != CaseState.RECOVERED),
        "late_recovery_after_ttl": sum(1 for o in outcomes if o.late_recovery_after_ttl),
        "late_recovery_note": (
            f"{sum(1 for o in outcomes if o.late_recovery_after_ttl)} cases paid after being marked lost "
            "— not counted in recovery rate."
        ),
        "diagnosis_method_split": {
            "rule": sum(1 for d in diagnoses if d.method == "rule"),
            "llm": sum(1 for d in diagnoses if d.method == "llm"),
            "fallback": sum(1 for d in diagnoses if d.method == "fallback"),
        },
        "computed_at": now.isoformat(),
    }
