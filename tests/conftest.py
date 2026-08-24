from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings
from app.db.session import install_append_only_guards


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    """Keep tests deterministic regardless of the host machine's credentials.

    Two leak paths are closed here:
    - exported shell vars (this dev box exports ANTHROPIC_API_KEY globally);
    - the gitignored ``.env`` file, which pydantic-settings reads from disk even
      after the matching process env var is deleted. pydantic-settings gives
      process env vars precedence over dotenv files, so forcing an *empty*
      value pins each subsystem to its mock/disabled mode no matter what
      credentials the developer's own .env carries.
    """
    for var in ("ANTHROPIC_API_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
        monkeypatch.setenv(var, "")
    # Sender registration back to documented defaults (a demo .env may verify them).
    monkeypatch.setenv("SMS_SENDER_VERIFIED", "false")
    monkeypatch.setenv("WHATSAPP_SENDER_VERIFIED", "false")
    # An empty DATABASE_URL would break engine creation — delete instead of
    # shadowing so settings fall back to its own default.
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
