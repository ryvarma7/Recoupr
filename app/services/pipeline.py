"""Case processing pipeline — the synchronous heart.

process_case() runs diagnosis → decision → guardrail check in one call
(spec §5.2), writing an AuditLogEntry at every step. Execution goes through the
gate-approved path only; a blocked proposal falls straight to escalation/stop
(v1 behaviour — no re-proposal loop). attempts_count increments ONLY on actual
execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.agents.decision import DecisionProposal, decide
from app.agents.diagnosis import DiagnosisResult, DiagnosisUnavailable, diagnose, fallback_diagnose
from app.agents.state_machine import audit, transition
from app.core.clock import as_naive_utc
from app.core.config import get_settings
from app.guardrails.checks import GateContext, check_guardrails
from app.guardrails.policy import PolicySnapshot, quiet_hours_resume_utc
from app.models.entities import (
    Action,
    Actor,
    Case,
    CaseState,
    Customer,
    Decision,
    DecisionStatus,
    Diagnosis,
    Event,
    EventType,
    FlowType,
    GuardrailCheck,
    GuardrailPolicy,
    Merchant,
    Outcome,
    OutcomeType,
)
from app.payments.client import ExecutionError, PaymentClient, get_payment_client
from app.payments.messages import render_recovery_message

logger = logging.getLogger(__name__)

# Gate violations that are pure timing constraints. A block consisting only of
# these DEFERS the case to the resume instant instead of escalating — waking a
# human for "it's 2 a.m." or "the cooldown hasn't elapsed" would be wrong.
TIMING_RULES = frozenset({"quiet_hours", "cooldown_active"})


def ensure_default_merchant(session: Session) -> tuple[Merchant, GuardrailPolicy]:
    """Get-or-create the demo merchant + spec-default guardrail policy."""
    policy = session.exec(select(GuardrailPolicy)).first()
    if policy is None:
        policy = GuardrailPolicy(name="default")
        session.add(policy)
        session.flush()
    merchant = session.exec(select(Merchant)).first()
    if merchant is None:
        merchant = Merchant(name="Meera Candles", guardrail_policy_id=policy.id)
        session.add(merchant)
        session.flush()
    return merchant, policy


def create_case_for_event(session: Session, event: Event, *, now: datetime) -> Case:
    """Ingest an at-risk event into a new Case, snapshotting the live policy."""
    now = as_naive_utc(now)
    merchant, policy_row = ensure_default_merchant(session)
    customer = _ensure_customer(session, event)

    case = Case(
        display_ref=f"CS-{event.id:06d}",
        event_id=event.id,  # type: ignore[arg-type]
        merchant_id=merchant.id,  # type: ignore[arg-type]
        flow_type=_flow_for(event),
        amount=event.amount,
        order_id=event.order_id,
        subscription_id=event.subscription_id,
        customer_id=customer.id,  # type: ignore[arg-type]
        policy_snapshot=policy_row.snapshot(),
        case_deadline_at=now + timedelta(days=policy_row.case_ttl_days),
    )
    session.add(case)
    session.flush()

    _link_related_cases(session, case)
    audit(
        session,
        case,
        actor=Actor.SYSTEM,
        summary=f"case created from {event.type.value} event {event.source_event_id}",
        after=CaseState.NEW,
        now=now,
    )
    return case


def process_case(
    session: Session,
    case: Case,
    *,
    now: datetime,
    latest_failure_reason: str | None = None,
) -> Case:
    """Run the full diagnosis → decision → gate → execute cycle synchronously."""
    settings = get_settings()
    now = as_naive_utc(now)  # single normalization point for all caller conventions
    client = get_payment_client()
    event = session.get(Event, case.event_id)
    customer = session.get(Customer, case.customer_id)
    policy = PolicySnapshot(case.policy_snapshot)
    # Normalize any tz-aware datetimes the case arrived with (e.g. created via
    # an aware utcnow()) so every comparison below is naive-UTC-consistent.
    for field_name in ("last_action_at", "case_deadline_at", "deferred_until"):
        value = getattr(case, field_name)
        if value is not None and value.tzinfo is not None:
            setattr(case, field_name, as_naive_utc(value))
    case.deferred_until = None  # being reprocessed now; reset until a new block says otherwise

    loop_back = case.state == CaseState.AWAITING_OUTCOME
    if not loop_back:
        transition(
            session, case, CaseState.PROCESSING,
            actor=Actor.SYSTEM, summary="pipeline picked up case", now=now,
        )
    else:
        transition(
            session, case, CaseState.PROCESSING,
            actor=Actor.SYSTEM,
            summary=f"retry wake-up: attempt {case.attempts_count + 1} of {policy.max_retries_per_case}",
            now=now,
        )

    # ── 1. Diagnosis ─────────────────────────────────────────────────────────
    try:
        if event is not None:
            result = diagnose(event, latest_failure_reason=latest_failure_reason)
        else:
            result = fallback_diagnose()
    except DiagnosisUnavailable as exc:
        _escalate(session, case, reason=f"diagnosis unavailable ({exc.reason})", now=now)
        return case

    session.add(Diagnosis(
        case_id=case.id, root_cause_category=result.root_cause_category,
        confidence=result.confidence, method=result.method,
        reasoning=result.reasoning[:280],
    ))
    audit(session, case, actor=Actor.AGENT,
          summary=f"diagnosed '{result.root_cause_category}' "
                  f"(confidence {result.confidence:.2f}, method={result.method}): {result.reasoning}",
          now=now)

    # ── 2. Decision ──────────────────────────────────────────────────────────
    gate_ctx = _gate_context(session, case, customer, now, settings)
    proposal: DecisionProposal = decide(case, policy, gate_ctx, result, now=now, settings=settings)

    action_name = proposal.proposed_action.split(":", 1)[0]
    params = dict(proposal.action_params)
    if action_name == "send_payment_link":
        params.update({
            "amount": case.amount,
            "order_ref": case.order_id or case.subscription_id or "",
            "expire_hours": policy.payment_link_expiry_hours,
            "single_use": policy.payment_link_single_use,
            "hosted_domain": "rzp.io",
            "language": proposal.message_language or "en",
            "tone": proposal.message_tone or "friendly",
        })

    decision = Decision(
        case_id=case.id, proposed_action=action_name, action_params=params,
        message_language=proposal.message_language, message_tone=proposal.message_tone,
        reasoning=proposal.reasoning[:280],
    )
    session.add(decision)
    session.flush()
    audit(session, case, actor=Actor.AGENT,
          summary=f"proposed {action_name} ({proposal.decided_by}): {proposal.reasoning}"[:500],
          now=now)

    # ── 3. Guardrail gate ────────────────────────────────────────────────────
    verdict = check_guardrails(case, decision, policy, gate_ctx)
    session.add(GuardrailCheck(
        decision_id=decision.id, case_id=case.id,
        passed=verdict.approved, violated_rules=list(verdict.violated_rules),
    ))
    if verdict.approved:
        audit(session, case, actor=Actor.SYSTEM, summary="guardrail check passed", now=now)
    else:
        audit(session, case, actor=Actor.SYSTEM,
              summary=f"guardrail check BLOCKED proposal — violated rules: {', '.join(verdict.violated_rules)}",
              now=now)
        decision.status = DecisionStatus.BLOCKED
        # Timing-only blocks defer instead of escalating; anything else (caps,
        # consent, channel policy, content rules) is a real refusal → human.
        if set(verdict.violated_rules) <= TIMING_RULES:
            resume_at = _timing_resume_at(now, case, policy, settings.tz)
            if resume_at is not None:
                case.deferred_until = resume_at
                transition(
                    session, case, CaseState.AWAITING_OUTCOME,
                    actor=Actor.SYSTEM,
                    summary=f"deferred until {resume_at.isoformat()} "
                            f"({', '.join(verdict.violated_rules)}); no attempt consumed",
                    now=now,
                )
                return case
        _escalate(session, case, reason="guardrail block: " + ", ".join(verdict.violated_rules), now=now)
        return case

    # ── 4. Execution ─────────────────────────────────────────────────────────
    try:
        _execute(session, case, decision, client, now=now)
    except ExecutionError as exc:
        logger.error("execution failed for %s: %s", case.display_ref, exc)
        _escalate(session, case, reason=f"execution failed: {exc}", now=now)
    return case


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _execute(session: Session, case: Case, decision: Decision, client: PaymentClient, *, now: datetime) -> None:
    action_type = decision.proposed_action

    if action_type == "stop":
        action = Action(case_id=case.id, decision_id=decision.id, action_type="stop",
                        status="EXECUTED", executed_at=now)
        session.add(action)
        decision.status = DecisionStatus.EXECUTED
        reason = str(decision.action_params.get("reason", "agent determined no further action warranted"))
        outcome = Outcome(case_id=case.id, outcome_type=OutcomeType.STOPPED, detail=reason)
        session.add(outcome)
        transition(session, case, CaseState.STOPPED_UNRECOVERABLE, actor=Actor.AGENT,
                   summary=f"stopped: {reason}", now=now)
        case.terminal_reason = reason
        return

    if action_type == "escalate_human":
        _escalate(session, case, reason=str(decision.action_params.get("reason", "human review requested")), now=now)
        return

    if action_type == "send_payment_link":
        link = client.create_payment_link(
            amount=case.amount,
            currency="INR",
            reference_id=f"case:{case.id}",
            description=f"Order {case.order_id}"[:255] if case.order_id else f"Case {case.display_ref}",
            expire_seconds=int(decision.action_params["expire_hours"]) * 3600,
            single_use=bool(decision.action_params["single_use"]),
        )
        body = render_recovery_message(
            case=case,
            language=decision.action_params.get("language", "en"),
            tone=decision.action_params.get("tone", "friendly"),
            short_url=link["short_url"],
        )
        channel = decision.action_params.get("channel", "email")
        session.add(Action(
            case_id=case.id, decision_id=decision.id, action_type="send_payment_link",
            status="EXECUTED", external_ref=link["id"], message_body=body, executed_at=now,
        ))
        decision.status = DecisionStatus.EXECUTED
        case.messages_sent_count += 1
        audit(session, case, actor=Actor.AGENT,
              summary=f"payment link sent via {channel} ({link['id']}) [{decision.action_params.get('language')}/{decision.action_params.get('tone')}]", now=now)

    elif action_type == "retry_mandate_charge":
        result = client.retry_mandate(
            subscription_id=case.subscription_id or "",
            amount=case.amount,
            reference_id=f"case:{case.id}",
        )
        session.add(Action(
            case_id=case.id, decision_id=decision.id, action_type="retry_mandate_charge",
            status="EXECUTED", external_ref=result["id"], executed_at=now,
        ))
        decision.status = DecisionStatus.EXECUTED
        audit(session, case, actor=Actor.AGENT,
              summary=f"mandate retry submitted on {case.subscription_id} ({result['id']})", now=now)

    else:  # unreachable — gate rejects unknown actions
        raise ExecutionError(f"unknown action type {action_type}")

    # Only real executions consume an attempt and start the cooldown clock.
    case.attempts_count += 1
    case.last_action_at = now
    transition(session, case, CaseState.AWAITING_OUTCOME, actor=Actor.AGENT,
               summary=f"action executed; awaiting outcome (attempt {case.attempts_count}/{PolicySnapshot(case.policy_snapshot).max_retries_per_case})",
               now=now)


def _timing_resume_at(now: datetime, case: Case, policy: PolicySnapshot, tz) -> datetime | None:
    """Earliest instant when every timing violation has cleared — naive UTC."""
    candidates: list[datetime] = []
    quiet_resume = quiet_hours_resume_utc(now, policy, tz)
    if quiet_resume is not None:
        candidates.append(quiet_resume)
    if case.last_action_at is not None:
        cooldown_end = case.last_action_at + timedelta(hours=policy.retry_cooldown_hours)
        if cooldown_end > now:
            candidates.append(cooldown_end)
    return max(candidates) if candidates else None


def _escalate(session: Session, case: Case, *, reason: str, now: datetime) -> None:
    """Escalation is terminal for automation; a human may still approve-and-send."""
    session.add(Action(
        case_id=case.id, decision_id=None, action_type="escalate_human",
        status="PENDING_APPROVAL", actor=Actor.SYSTEM,
    ))
    session.add(Outcome(case_id=case.id, outcome_type=OutcomeType.ESCALATED, detail=reason))
    transition(session, case, CaseState.ESCALATED_TO_HUMAN, actor=Actor.SYSTEM,
               summary=f"escalated to human: {reason}", now=now)
    case.terminal_reason = reason


# ---------------------------------------------------------------------------
# Context assembly helpers
# ---------------------------------------------------------------------------

def _gate_context(session: Session, case: Case, customer: Customer | None, now: datetime, settings) -> GateContext:
    verified = set()
    if settings.sms_sender_verified:
        verified.add("sms")
    if settings.whatsapp_sender_verified:
        verified.add("whatsapp")
    verified.add("email")  # merchant's own domain; transactional, always available

    sent_rows = session.exec(
        select(Action).where(
            Action.case_id == case.id,  # type: ignore[arg-type]
            Action.action_type == "send_payment_link",
            Action.status == "EXECUTED",
        )
    ).all()
    return GateContext(
        now=now,
        tz=settings.tz,
        customer_sms_opt_in=bool(customer and customer.sms_opt_in),
        customer_whatsapp_opt_in=bool(customer and customer.whatsapp_opt_in),
        verified_channels=frozenset(verified),
        messages_sent_at=tuple(a.executed_at for a in sent_rows if a.executed_at is not None),
    )


def _flow_for(event: Event) -> FlowType:
    mapping = {
        EventType.PAYMENT_FAILED: FlowType.PAYMENT_FAILURE,
        EventType.CHECKOUT_ABANDONED: FlowType.CHECKOUT_ABANDONMENT,
        EventType.SUBSCRIPTION_CHARGE_FAILED: FlowType.SUBSCRIPTION_MANDATE,
    }
    if event.type not in mapping:
        raise ValueError(f"event type {event.type.value} does not open a case")
    return mapping[event.type]


def _ensure_customer(session: Session, event: Event) -> Customer:
    if event.customer_id is not None:
        existing = session.get(Customer, event.customer_id)
        if existing is not None:
            return existing
    customer = Customer(
        masked_phone="+91•••••0000",
        masked_email="c•••@example.com",
        consent_flags={"sms_opt_in": False, "whatsapp_opt_in": False},
    )
    session.add(customer)
    session.flush()
    event.customer_id = customer.id
    return customer


def _link_related_cases(session: Session, case: Case) -> None:
    """Two cases on the same order are linked, not treated as independent."""
    if case.order_id is None:
        return
    others = session.exec(
        select(Case).where(Case.order_id == case.order_id, Case.id != case.id)  # type: ignore[arg-type,union-attr]
    ).all()
    for other in others:
        if case.id not in other.related_case_ids:
            other.related_case_ids = [*other.related_case_ids, case.id]
        if other.id not in case.related_case_ids:
            case.related_case_ids = [*case.related_case_ids, other.id]


def record_recovery(session: Session, case: Case, *, payment_id: str, amount: int, recovered_at: datetime) -> None:
    """Mark RECOVERED — only ever from a real, matched payment event."""
    recovered_at = as_naive_utc(recovered_at)
    session.add(Outcome(
        case_id=case.id, outcome_type=OutcomeType.RECOVERED,
        amount_recovered=amount, recovered_at=recovered_at, matched_payment_id=payment_id,
    ))
    if case.state == CaseState.ESCALATED_TO_HUMAN:
        transition(session, case, CaseState.AWAITING_OUTCOME, actor=Actor.HUMAN,
                   summary="human approval recorded; observing approved action outcome", now=recovered_at)
    transition(session, case, CaseState.RECOVERED, actor=Actor.SYSTEM,
               summary=f"matched payment {payment_id} of {amount} paise recovered", now=recovered_at)
    case.terminal_reason = None


def mark_lost(session: Session, case: Case, *, now: datetime) -> None:
    now = as_naive_utc(now)
    session.add(Outcome(case_id=case.id, outcome_type=OutcomeType.LOST))
    transition(session, case, CaseState.LOST, actor=Actor.SYSTEM,
               summary=f"case TTL elapsed with no recovery", now=now)
    case.terminal_reason = f"TTL of {PolicySnapshot(case.policy_snapshot).case_ttl_days} days elapsed without recovery"


def approve_and_send(session: Session, case: Case, *, now: datetime) -> Case:
    """Human approves the pending escalated proposal → executes it (actor=human)."""
    now = as_naive_utc(now)
    if case.state != CaseState.ESCALATED_TO_HUMAN:
        raise ValueError(f"case {case.display_ref} is not awaiting human approval")
    pending = session.exec(
        select(Action).where(
            Action.case_id == case.id,  # type: ignore[arg-type]
            Action.status == "PENDING_APPROVAL",
        ).order_by(Action.id.desc())  # type: ignore[union-attr]
    ).first()
    if pending is None:
        raise ValueError(f"no pending approval found for {case.display_ref}")

    pending.status = "EXECUTED"
    pending.actor = Actor.HUMAN
    pending.executed_at = now
    audit(session, case, actor=Actor.HUMAN, summary="human approved escalated action", now=now)
    transition(session, case, CaseState.AWAITING_OUTCOME, actor=Actor.HUMAN,
               summary="approved by human; automation observes outcome", now=now)
    return case
