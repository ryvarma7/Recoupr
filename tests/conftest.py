from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings
from app.db.session import install_append_only_guards


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    """Keep tests deterministic regardless of the host machine's credentials.

    This dev box has ANTHROPIC_API_KEY exported globally; without this fixture
    'LLM_DISABLED' mode silently wouldn't be disabled under pytest.
    """
    for var in (
        "ANTHROPIC_API_KEY",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    # TestClient startup must not spawn background scheduler threads mid-test.
    monkeypatch.setenv("RECOUPR_SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    install_append_only_guards(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine):
    with Session(engine) as session:
        yield session
