"""FastAPI entrypoint — Recoupr backend."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel

from app.api.routes import router as api_router
from app.api.webhooks import router as webhook_router
from app.core.clock import utcnow
from app.core.config import get_settings, subsystem_modes
from app.db.session import get_session, install_append_only_guards, make_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s │ %(message)s",
)
logger = logging.getLogger("recoupr")

app = FastAPI(title="Recoupr", version="0.1.0", description="AI revenue-recovery agent system (test mode only)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(webhook_router)
app.include_router(api_router)


@app.on_event("startup")
def startup() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    install_append_only_guards(engine)
    if settings.database_url.startswith("sqlite"):
        # zero-setup dev/demo convenience; Postgres deployments use Alembic.
        SQLModel.metadata.create_all(engine)
    get_session._engine = engine  # type: ignore[attr-defined]

    logger.info("Recoupr starting — subsystem modes:")
    for subsystem, mode in subsystem_modes(settings).items():
        logger.info("  %-18s %s", subsystem, mode)

    if settings.scheduler_enabled:
        _start_maintenance_scheduler(settings)


def _start_maintenance_scheduler(settings) -> None:
    """Background TTL sweep + deferred requeue on the embedded APScheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from sqlmodel import Session

    from app.services.maintenance import run_maintenance

    def tick() -> None:
        engine = getattr(get_session, "_engine", None)
        if engine is None:
            return
        try:
            with Session(engine) as session:
                report = run_maintenance(session, now=utcnow())
            if report["ttl_swept"] or report["deferred_requeued"]:
                logger.info("maintenance pass: %s", report)
        except Exception:
            logger.exception("maintenance pass failed")

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        tick,
        "interval",
        seconds=settings.maintenance_interval_seconds,
        id="recoupr-maintenance",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "maintenance scheduler started (every %ss); disable with RECOUPR_SCHEDULER_ENABLED=false",
        settings.maintenance_interval_seconds,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
