"""End-to-end batch simulation smoke test — believable numbers, zero violations."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.session import install_append_only_guards
from app.simulation.batch import run_batch


@pytest.fixture()
def fresh_db():
    """A callable yielding an independent session — seed-determinism needs two."""
    def _make() -> Session:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        install_append_only_guards(engine)
        SQLModel.metadata.create_all(engine)
        return Session(engine)
    return _make


def test_batch_produces_believable_report(fresh_db):
    summary = run_batch(fresh_db(), count=60, seed=2026)

    # ── shape ─────────────────────────────────────────────────────────────
    assert summary["cases_total"] == 60
    assert summary["recovered"] + summary["lost"] + summary["escalated"] + summary[
        "stopped_unrecoverable"
    ] + summary["pending"] == 60

    # ── honesty invariants ────────────────────────────────────────────────
    assert summary["guardrail_violations"] == 0, (
        "every executed action must have a passing guardrail check on record"
    )
    assert summary["recovery_rate"] == round(
        summary["recovered"] / max(summary["recovered"] + summary["lost"], 1), 4
    )
    # recoverable-labeled cases convert well above unrecoverable ones, but not
    # near-perfectly: probability compounds across observation wakes. The blended
    # rate must land in a credible dunning band — near 0% or near 90% means the
    # wiring or the calibration is broken.
    assert 0.20 <= summary["recovery_rate"] <= 0.65
    assert summary["escalated_pct"] <= 25.0, (
        "escalation is for anomalies, not routine non-response"
    )

    # ground-truth accounting is present and coherent
    gt = summary["ground_truth"]
    assert gt["labeled_cases"] == 60
    assert abs(gt["recoverable_share"] - gt["recoverable_labeled"] / 60) < 1e-9


def test_batch_is_seed_deterministic(fresh_db):
    first = run_batch(fresh_db(), count=30, seed=7)
    second = run_batch(fresh_db(), count=30, seed=7)
    assert (first["recovered"], first["lost"], first["escalated"]) == (
        second["recovered"],
        second["lost"],
        second["escalated"],
    )
