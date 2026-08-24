"""Database engine / session factory plus the append-only enforcement for AuditLogEntry."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.core.config import get_settings
from app.models.entities import AuditLogEntry


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    settings = get_settings()
    url = url or settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=echo, connect_args=connect_args)


def install_append_only_guards(engine: Engine) -> None:
    """Raise if ORM flush/update/delete ever tries to mutate an audit row.

    The codebase never issues such statements; this makes the invariant hold even
    against future mistakes. (A Postgres-level trigger hardening ships as a
    documented production step — see README known-limitations.)
    """

    def _block_update(mapper, connection, target):
        raise RuntimeError("AuditLogEntry is append-only: UPDATE denied")

    def _block_delete(mapper, connection, target):
        raise RuntimeError("AuditLogEntry is append-only: DELETE denied")

    event.listen(AuditLogEntry, "before_update", _block_update)
    event.listen(AuditLogEntry, "before_delete", _block_delete)


def make_session(engine: Engine) -> Session:
    return Session(engine)


def get_session():  # FastAPI dependency
    settings = get_settings()
    engine = getattr(get_session, "_engine", None)
    if engine is None:
        engine = make_engine(settings.database_url)
        install_append_only_guards(engine)
        get_session._engine = engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        yield session
