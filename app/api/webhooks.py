"""Razorpay webhook receiver.

Signature policy (fail closed):
- Secret configured → HMAC-SHA256 of the raw body must match X-Razorpay-Signature,
  compared with hmac.compare_digest. A mismatch is rejected 400 — always.
- No secret configured → bypassed ONLY while Razorpay runs in mock mode, loudly
  logged on every bypassed delivery. Real-key mode without a configured secret
  refuses deliveries entirely (503) rather than accepting unverified traffic.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.core.clock import utcnow
from app.core.config import get_settings
from app.db.session import get_session
from app.models.entities import CaseState, Event, EventType
from app.services.pipeline import create_case_for_event, process_case, record_recovery

logger = logging.getLogger(__name__)
router = APIRouter()

# Webhook event names this receiver understands, mapped to internal types.
FAILURE_EVENTS = {
    "payment.failed": EventType.PAYMENT_FAILED,
    "checkout.abandoned": EventType.CHECKOUT_ABANDONED,
    "subscription.charge.failed": EventType.SUBSCRIPTION_CHARGE_FAILED,
}
SUCCESS_EVENTS = {"payment.captured", "payment_link.paid", "subscription.charged"}


def _verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/razorpay")
async def receive_razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    raw_body = await request.body()

    # ── signature enforcement ────────────────────────────────────────────
    if settings.razorpay_webhook_secret:
        if not x_razorpay_signature:
            raise HTTPException(status_code=400, detail="missing X-Razorpay-Signature")
        if not _verify_signature(raw_body, x_razorpay_signature, settings.razorpay_webhook_secret):
            logger.warning("webhook rejected: invalid signature")
            raise HTTPException(status_code=400, detail="invalid signature")
    elif settings.razorpay_mock:
        logger.warning("WEBHOOK SIGNATURE BYPASSED — no RAZORPAY_WEBHOOK_SECRET configured (mock mode)")
    else:
        raise HTTPException(
            status_code=503,
            detail="RAZORPAY_WEBHOOK_SECRET must be configured when running against real Razorpay keys",
        )

    # ── parse ──────────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="body is not valid JSON") from exc

    event_name = payload.get("event") or payload.get("type")
    source_id = str(payload.get("id") or payload.get("request_id") or "")
    if not event_name or not source_id:
        raise HTTPException(status_code=400, detail="payload missing 'event' and idempotency 'id'")

    entity = payload.get("payload", {})

    def _payment_entity(value):
        if not isinstance(value, dict):
            return {}
        payment = value.get("payment")
        if isinstance(payment, dict) and isinstance(payment.get("entity"), dict):
            return payment["entity"]
        return payment if isinstance(payment, dict) else value.get("order", value)

    # ── idempotency: duplicate deliveries must never double-act ───────────
    existing = session.exec(select(Event).where(Event.source_event_id == source_id)).first()
    if existing is not None:
        logger.info("duplicate webhook delivery %s ignored", source_id)
        return {"status": "duplicate", "source_event_id": source_id}

    now = utcnow()

    if event_name in FAILURE_EVENTS:
        error = entity.get("error", {}) if isinstance(entity, dict) else {}
        payment = _payment_entity(entity)
        if not error and isinstance(payment, dict):
            error = payment.get("error", {})
        event = Event(
            source_event_id=source_id,
            type=FAILURE_EVENTS[event_name],
            razorpay_payload_ref=str(payload.get("id", "")),
            amount=int(payment.get("amount") or payload.get("amount") or 0),
            currency=str(payload.get("currency", "INR")),
            order_id=payment.get("order_id") or payload.get("order_id"),
            subscription_id=payment.get("subscription_id") or payload.get("subscription_id"),
            error_code=(error.get("code") or payload.get("error_code")),
            error_description=(error.get("description") or payload.get("error_description")),
            payload=payload,
            occurred_at=now,
        )
        session.add(event)
        session.flush()
        case = create_case_for_event(session, event, now=now)
        # LLM retries use exponential backoff; keep that synchronous work off
        # the event loop so one webhook cannot freeze other deliveries.
        await run_in_threadpool(process_case, session, case, now=now)
        session.commit()
        return {"status": "accepted", "case_ref": case.display_ref, "state": case.state.value}

    if event_name in SUCCESS_EVENTS:
        payment = _payment_entity(entity)
        payment_id = str(payment.get("id") or source_id)
        amount = int(payment.get("amount") or 0)
        reference = (
            payment.get("notes", {}).get("reference_id")
            or payment.get("reference_id")
            or ""
        )
        matched = _match_open_case(session, reference, order_id=payment.get("order_id"))
        if matched is None:
            logger.info("success event %s matched no open case (reference=%r)", payment_id, reference)
            return {"status": "ignored", "reason": "no matching open case"}

        try:
            record_recovery(session, matched, payment_id=payment_id, amount=amount, recovered_at=now)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        late = matched.state == CaseState.LOST
        return {"status": "late_recovery_after_ttl" if late else "recovered",
                "case_ref": matched.display_ref, "matched_payment_id": payment_id}

    return {"status": "ignored", "reason": f"unhandled event type {event_name}"}


def _match_open_case(session: Session, reference: str, order_id: str | None):
    """Match a successful payment back to its case via link reference_id (`case:{id}`)."""
    from app.models.entities import Case, CaseState

    if reference.startswith("case:"):
        case_id = int(reference.split(":", 1)[1])
        case = session.get(Case, case_id)
        if case is not None and case.state in (CaseState.AWAITING_OUTCOME, CaseState.LOST):
            return case

    if order_id:
        candidates = session.exec(
            select(Case).where(Case.order_id == order_id, Case.state.in_((CaseState.AWAITING_OUTCOME, CaseState.LOST)))
        ).all()
        if candidates:
            return candidates[0]
    return None
