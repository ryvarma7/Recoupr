"""Webhook receiver: signature enforcement (fail closed) + idempotent ingestion."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "webhook_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _failure_payload(source_id: str = "evt_hook001") -> bytes:
    return json.dumps({
        "id": source_id,
        "event": "payment.failed",
        "payload": {
            "payment": {
                "id": "pay_hook1",
                "amount": 150_000,
                "order_id": "order_hook77",
            },
            "error": {"code": "gateway_timeout", "description": "Payment timed out at bank end"},
        },
    }).encode()


def _signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_missing_signature_rejected_when_secret_configured(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test_123")
    get_settings.cache_clear()
    response = client.post("/webhooks/razorpay", content=_failure_payload())
    assert response.status_code == 400


def test_tampered_signature_rejected_fail_closed(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test_123")
    get_settings.cache_clear()
    body = _failure_payload()
    forged = _signature(b"tampered-payload", "whsec_test_123")
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": forged},
    )
    assert response.status_code == 400


def test_valid_signature_accepted_and_processed(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test_123")
    get_settings.cache_clear()
    body = _failure_payload()
    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": _signature(body, "whsec_test_123")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["state"] in ("AWAITING_OUTCOME", "ESCALATED_TO_HUMAN")


def test_duplicate_delivery_never_double_acts(client):
    """Same webhook delivered twice → second is an idempotent no-op."""
    body = _failure_payload()
    first = client.post("/webhooks/razorpay", content=body)
    assert first.status_code == 200
    case_ref = first.json()["case_ref"]

    second = client.post("/webhooks/razorpay", content=body)
    assert second.json()["status"] == "duplicate"

    listing = client.get("/cases").json()
    refs = [c["display_ref"] for c in listing["cases"]]
    assert refs.count(case_ref) == 1


def test_real_mode_without_webhook_secret_refuses_deliveries(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "testsecret")
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()
    response = client.post("/webhooks/razorpay", content=_failure_payload())
    assert response.status_code == 503


def test_success_event_matches_case_via_reference_id(client):
    """A payment.captured carrying notes.reference_id=case:{id} closes its case as RECOVERED."""
    # 1. failure → case created & link sent
    failure = client.post("/webhooks/razorpay", content=_failure_payload()).json()
    cases = client.get("/cases").json()["cases"]
    case_id = next(c["id"] for c in cases if c["display_ref"] == failure["case_ref"])
    detail = client.get(f"/cases/{case_id}").json()
    assert detail["state"] == "AWAITING_OUTCOME"

    # 2. success arrives referencing that case
    success = json.dumps({
        "id": "evt_paid001",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "id": "pay_captured9",
                "amount": 150_000,
                "order_id": "order_hook77",
                "notes": {"reference_id": f"case:{case_id}"},
            }
        },
    }).encode()
    response = client.post("/webhooks/razorpay", content=success)
    assert response.status_code == 200
    assert response.json()["status"] == "recovered"
    assert response.json()["matched_payment_id"] == "pay_captured9"

    detail = client.get(f"/cases/{case_id}").json()
    assert detail["state"] == "RECOVERED"


def test_out_of_order_success_first_is_ignored_then_recovery_still_works(client):
    """Success arriving before any open case must not crash or fabricate one."""
    orphan = json.dumps({
        "id": "evt_orphan1",
        "event": "payment.captured",
        "payload": {"payment": {"id": "pay_orphan", "amount": 100, "notes": {}}},
    }).encode()
    ignored = client.post("/webhooks/razorpay", content=orphan)
    assert ignored.json()["status"] == "ignored"

    # normal flow still functions afterwards
    accepted = client.post("/webhooks/razorpay", content=_failure_payload("evt_after_orphan"))
    assert accepted.json()["status"] == "accepted"
