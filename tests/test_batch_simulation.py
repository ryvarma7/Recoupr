"""End-to-end batch simulation smoke test — believable numbers, zero violations."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.session import install_append_only_guards
from app.simulation.batch import run_batch

# Frozen replay clock — batches must be fully deterministic under a fixed now.
FROZEN_NOW = datetime(2026, 8, 24, 12, 0)


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
    session = fresh_db()
    summary = run_batch(session, count=150, seed=2026, now=FROZEN_NOW)

    # ── shape ─────────────────────────────────────────────────────────────
    assert summary["cases_total"] == 150
    assert summary["recovered"] + summary["lost"] + summary["escalated"] + summary[
        "stopped_unrecoverable"
    ] + summary["pending"] == 150

    # ── honesty invariants ────────────────────────────────────────────────
    assert summary["guardrail_violations"] == 0, (
        "every executed action must have a passing guardrail check on record"
    )
    assert summary["recovery_rate"] == round(
        summary["recovered"] / max(summary["recovered"] + summary["lost"], 1), 4
    )
    # The settled cohort (full observation window elapsed) is the censoring-free
    # rate — the global rate over-weights the loss-free recent tail. It must land
    # in a credible dunning band: near 0% or near 90% means broken calibration.
    # Calibrated across seeds 2026/7/99/123 at count=150: 0.51–0.62.
    settled = summary["settled_cohort"]
    assert settled["cases"] > 0, "a 28-day replay must fully resolve some cases"
    assert (
        settled["recovered"] + settled["lost"] + settled["escalated"] + settled["stopped"]
        == settled["cases"]
    )
    assert 0.35 <= settled["recovery_rate"] <= 0.70, (
        f"settled recovery rate {settled['recovery_rate']} outside credible band"
    )
    assert summary["escalated_pct"] <= 25.0, (
        "escalation is for anomalies, not routine non-response"
    )
    # The replay never fabricates the future: some cases are legitimately open.
    assert summary["pending"] > 0

    # Regression (negative-TTR bug): recoveries are stamped inside the case's
    # lifetime and never beyond the replay clock.
    from sqlmodel import select

    from app.models.entities import Case, Outcome, OutcomeType

    for outcome in session.exec(select(Outcome)).all():
        if outcome.outcome_type == OutcomeType.RECOVERED and outcome.recovered_at:
            case = session.get(Case, outcome.case_id)
            assert case.created_at <= outcome.recovered_at <= FROZEN_NOW, (
                f"{case.display_ref}: created {case.created_at} vs recovered {outcome.recovered_at}"
            )

    # ground-truth accounting is present and coherent
    gt = summary["ground_truth"]
    assert gt["labeled_cases"] == 150
    assert abs(gt["recoverable_share"] - gt["recoverable_labeled"] / 150) < 1e-3  # share is rounded to 3dp


def test_batch_is_seed_deterministic(fresh_db):
    first = run_batch(fresh_db(), count=30, seed=7, now=FROZEN_NOW)
    second = run_batch(fresh_db(), count=30, seed=7, now=FROZEN_NOW)
    assert (first["recovered"], first["lost"], first["escalated"], first["pending"]) == (
        second["recovered"],
        second["lost"],
        second["escalated"],
        second["pending"],
    )
    assert first["settled_cohort"] == second["settled_cohort"]
