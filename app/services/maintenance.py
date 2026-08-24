"""Periodic maintenance pass — TTL sweep + deferred-case requeue.

Both the /maintenance endpoints and the embedded APScheduler job call this one
function, so a deployment without the scheduler (cron hitting the endpoint
instead) behaves identically.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlmodel import Session, select

from app.core.clock import as_naive_utc
from app.models.entities import Case, CaseState
from app.services.pipeline import mark_lost, process_case

logger = logging.getLogger(__name__)


def run_maintenance(session: Session, *, now: datetime) -> dict:
    now = as_naive_utc(now)

    # 1. TTL sweep: open cases past their deadline close as LOST.
    swept = 0
    for case in session.exec(select(Case)).all():
        if (
            case.state == CaseState.AWAITING_OUTCOME
            and case.case_deadline_at is not None
            and case.case_deadline_at <= now
        ):
            mark_lost(session, case, now=now)
            swept += 1

    # 2. Deferred requeue: timing-blocked cases whose retry window has opened.
    requeued = 0
    due = session.exec(
        select(Case).where(  # type: ignore[arg-type]
            Case.state == CaseState.AWAITING_OUTCOME,
            Case.deferred_until.is_not(None),  # type: ignore[union-attr]
        )
    ).all()
    for case in due:
        if case.deferred_until is not None and case.deferred_until <= now:
            process_case(session, case, now=now)
            requeued += 1

    session.commit()
    return {"ttl_swept": swept, "deferred_requeued": requeued}
