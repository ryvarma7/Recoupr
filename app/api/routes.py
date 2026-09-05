"""Read/act API surface for the dashboard — spec §8."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.db.session import get_session
from app.models.entities import (
    Action,
    AuditLogEntry,
    Case,
    CaseState,
    Decision,
    Diagnosis,
    GuardrailCheck,
    GuardrailPolicy,
    Outcome,
    SimulationRun,
)
from app.services.maintenance import run_maintenance
from app.services.metrics import compute_summary
from app.services.pipeline import approve_and_send
from app.simulation.batch import run_batch

router = APIRouter()

_STATE_ALIASES = {
    "RECOVERED": CaseState.RECOVERED,
    "LOST": CaseState.LOST,
    "ESCALATED": CaseState.ESCALATED_TO_HUMAN,
    "ESCALATED_TO_HUMAN": CaseState.ESCALATED_TO_HUMAN,
    "AWAITING": CaseState.AWAITING_OUTCOME,
    "AWAITING_OUTCOME": CaseState.AWAITING_OUTCOME,
    "NEW": CaseState.NEW,
    "PROCESSING": CaseState.PROCESSING,
    "STOPPED": CaseState.STOPPED_UNRECOVERABLE,
    "STOPPED_UNRECOVERABLE": CaseState.STOPPED_UNRECOVERABLE,
}


def _case_summary(case: Case) -> dict:
    return {
        "id": case.id,
        "display_ref": case.display_ref,
        "flow_type": case.flow_type.value,
        "state": case.state.value,
        "amount_paise": case.amount,
        "order_id": case.order_id,
        "subscription_id": case.subscription_id,
        "attempts_count": case.attempts_count,
        "messages_sent_count": case.messages_sent_count,
        "related_case_ids": case.related_case_ids,
        "terminal_reason": case.terminal_reason,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
    }


@router.get("/cases")
def list_cases(
    state: str | None = Query(default=None),
    flow: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    simulation_run_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    statement = select(Case).order_by(Case.updated_at.desc())  # type: ignore[union-attr]
    cases = session.exec(statement).all()
    if simulation_run_id is None:
        latest_run = session.exec(select(SimulationRun).order_by(SimulationRun.id.desc())).first()
        if latest_run is not None:
            simulation_run_id = latest_run.id
    if state:
        target = _STATE_ALIASES.get(state.upper())
        if target is None:
            raise HTTPException(status_code=400, detail=f"unknown state '{state}'")
        cases = [c for c in cases if c.state == target]
    if flow:
        cases = [c for c in cases if c.flow_type.value == flow.upper()]
    if start:
        cases = [c for c in cases if c.created_at >= start]
    if end:
        cases = [c for c in cases if c.created_at < end]
    if simulation_run_id is not None:
        cases = [c for c in cases if c.simulation_run_id == simulation_run_id]

    return {
        "total": len(cases),
        "cases": [_case_summary(c) for c in cases[offset : offset + limit]],
    }


@router.get("/cases/{case_id}")
def case_detail(case_id: int, session: Session = Depends(get_session)) -> dict:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")

    audit_rows = session.exec(
        select(AuditLogEntry).where(AuditLogEntry.case_id == case_id).order_by(AuditLogEntry.timestamp)  # type: ignore[union-attr,arg-type]
    ).all()
    diagnoses = session.exec(select(Diagnosis).where(Diagnosis.case_id == case_id)).all()  # type: ignore[arg-type]
    decisions = session.exec(select(Decision).where(Decision.case_id == case_id)).all()  # type: ignore[arg-type]
    actions = session.exec(select(Action).where(Action.case_id == case_id)).all()  # type: ignore[arg-type]
    checks = session.exec(select(GuardrailCheck).where(GuardrailCheck.case_id == case_id)).all()  # type: ignore[arg-type]
    outcomes = session.exec(select(Outcome).where(Outcome.case_id == case_id)).all()  # type: ignore[arg-type]
    pending_action = next((a for a in actions if a.status == "PENDING_APPROVAL"), None)

    return {
        **_case_summary(case),
        "policy_snapshot": case.policy_snapshot,
        "audit_trail": [
            {
                "actor": row.actor.value,
                "summary": row.summary,
                "before_state": row.before_state,
                "after_state": row.after_state,
                "timestamp": row.timestamp.isoformat(),
            }
            for row in audit_rows
        ],
        "diagnoses": [
            {
                "root_cause_category": d.root_cause_category,
                "confidence": d.confidence,
                "method": d.method,
                "reasoning": d.reasoning,
            }
            for d in diagnoses
        ],
        "decisions": [
            {
                "proposed_action": d.proposed_action,
                "action_params": d.action_params,
                "message_language": d.message_language,
                "message_tone": d.message_tone,
                "reasoning": d.reasoning,
                "status": d.status.value,
            }
            for d in decisions
        ],
        "actions": [
            {
                "action_type": a.action_type,
                "status": a.status,
                "actor": a.actor.value if a.actor else "agent",
                "external_ref": a.external_ref,
                "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            }
            for a in actions
        ],
        "guardrail_checks": [
            {"passed": c.passed, "violated_rules": c.violated_rules} for c in checks
        ],
        "outcomes": [
            {
                "outcome_type": o.outcome_type.value,
                "amount_recovered_paise": o.amount_recovered,
                "recovered_at": o.recovered_at.isoformat() if o.recovered_at else None,
                "matched_payment_id": o.matched_payment_id,
                "detail": o.detail,
                "late_recovery_after_ttl": o.late_recovery_after_ttl,
            }
            for o in outcomes
        ],
        "can_approve": pending_action is not None and case.state == CaseState.ESCALATED_TO_HUMAN,
    }


class ApproveRequest(BaseModel):
    admin: bool = True


@router.post("/cases/{case_id}/approve")
def approve_case(case_id: int, body: ApproveRequest | None = None, session: Session = Depends(get_session)) -> dict:
    """Human approval executing the escalated proposal — actor recorded as human."""
    try:
        case = approve_and_send(session, session.get(Case, case_id), now=utcnow())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AttributeError as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc
    session.commit()
    return {"status": "approved", "case_ref": case.display_ref, "state": case.state.value}


@router.get("/metrics/summary")
def metrics_summary(session: Session = Depends(get_session)) -> dict:
    run = session.exec(select(SimulationRun).order_by(SimulationRun.id.desc())).first()
    return compute_summary(session, simulation_run_id=run.id if run else None)


@router.get("/exceptions")
def exceptions(session: Session = Depends(get_session)) -> dict:
    rows = session.exec(select(Case)).all()
    interesting = [
        c for c in rows
        if c.state in (CaseState.STOPPED_UNRECOVERABLE, CaseState.ESCALATED_TO_HUMAN)
    ]
    return {
        "total": len(interesting),
        "exceptions": [
            {
                **_case_summary(c),
                "state_tag": "STOPPED" if c.state == CaseState.STOPPED_UNRECOVERABLE else "ESCALATED",
            }
            for c in sorted(interesting, key=lambda c: c.updated_at, reverse=True)
        ],
    }


@router.get("/guardrail-log")
def guardrail_log(limit: int = Query(default=200, le=1000), session: Session = Depends(get_session)) -> dict:
    checks = session.exec(
        select(GuardrailCheck).order_by(GuardrailCheck.created_at.desc())  # type: ignore[union-attr]
    ).all()
    decisions = {
        d.id: d for d in session.exec(select(Decision)).all()
    }
    cases = {c.id: c for c in session.exec(select(Case)).all()}
    out = []
    for check in checks[:limit]:
        decision = decisions.get(check.decision_id)
        case = cases.get(check.case_id)
        out.append({
            "id": check.id,
            "case_ref": case.display_ref if case else str(check.case_id),
            "proposed_action": decision.proposed_action if decision else "?",
            "passed": check.passed,
            "violated_rules": check.violated_rules,
            "created_at": check.created_at.isoformat(),
        })
    return {"total": len(checks), "checks": out}


@router.get("/audit-log")
def audit_log(
    limit: int = Query(default=200, le=1000),
    offset: int = Query(default=0),
    session: Session = Depends(get_session),
) -> dict:
    rows = session.exec(
        select(AuditLogEntry).order_by(AuditLogEntry.timestamp.desc(), AuditLogEntry.id.desc())  # type: ignore[union-attr]
    ).all()
    cases = {c.id: c.display_ref for c in session.exec(select(Case)).all()}
    return {
        "total": len(rows),
        "entries": [
            {
                "case_ref": cases.get(row.case_id, str(row.case_id)),
                "actor": row.actor.value,
                "summary": row.summary,
                "before_state": row.before_state,
                "after_state": row.after_state,
                "timestamp": row.timestamp.isoformat(),
            }
            for row in rows[offset : offset + limit]
        ],
    }


@router.get("/policy")
def policy_view(session: Session = Depends(get_session)) -> dict:
    policy = session.exec(select(GuardrailPolicy)).first()
    if policy is None:
        raise HTTPException(status_code=404, detail="no policy configured")
    open_states = (CaseState.NEW, CaseState.PROCESSING, CaseState.AWAITING_OUTCOME)
    open_cases = [c for c in session.exec(select(Case)).all() if c.state in open_states]
    snapshot_versions: dict[str, int] = {}
    for c in open_cases:
        key = f"{c.policy_snapshot.get('name', 'default')}@{c.policy_snapshot.get('id', '?')}"
        snapshot_versions[key] = snapshot_versions.get(key, 0) + 1
    return {
        "current": policy.snapshot(),
        "open_case_snapshots": snapshot_versions,
    }


class BatchRequest(BaseModel):
    count: int = 200
    seed: int | None = None


@router.post("/simulate/batch")
def simulate_batch(body: BatchRequest, session: Session = Depends(get_session)) -> dict:
    count = max(10, min(body.count, 300))
    report = run_batch(session, count=count, seed=body.seed)
    return report


@router.post("/maintenance/ttl-sweep")
def ttl_sweep(session: Session = Depends(get_session)) -> dict:
    """TTL-only pass: close expired AWAITING_OUTCOME cases as LOST."""
    result = run_maintenance(session, now=utcnow())
    return {"swept": result["ttl_swept"]}


@router.post("/maintenance/tick")
def maintenance_tick(session: Session = Depends(get_session)) -> dict:
    """Full scheduler-equivalent pass: TTL sweep + deferred-case requeue.

    Deployments that disable the embedded scheduler can cron this endpoint;
    behavior is byte-for-byte the same as the background job.
    """
    return run_maintenance(session, now=utcnow())
