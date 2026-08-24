"""Real-client payload hygiene — the mock accepts Python objects the real API rejects.

Regression: a set literal in options.checkout.method passed the mock client but
broke the real SDK's JSON encoding at execution time (only visible with real
test-mode keys). The payload must be JSON-serializable, always.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import app.payments.client as payments_client_module
from app.core.config import get_settings
from app.payments.client import (
    RealRazorpayClient,
    get_payment_client,
    get_simulated_payment_client,
)


def _client_with_captured_payload():
    """RealRazorpayClient with the SDK stubbed; returns (client, captured list)."""
    with patch("razorpay.Client") as sdk_ctor:
        captured: list[dict] = []

        def create(data):
            captured.append(data)
            return {"id": "plink_test123", "short_url": "https://rzp.io/i/test123"}

        sdk = sdk_ctor.return_value
        sdk.payment_link.create.side_effect = create
        client = RealRazorpayClient("rzp_test_unittest", "secret")
    return client, captured


def test_payment_link_payload_is_json_serializable():
    client, captured = _client_with_captured_payload()
    link = client.create_payment_link(
        amount=250_000,
        currency="INR",
        reference_id="case:1",
        description="Order order_1",
        expire_seconds=24 * 3600,
        single_use=True,
    )
    assert link["id"] == "plink_test123"
    # The exact contract the real SDK needs — would have raised TypeError on a set.
    json.dumps(captured[0])


def test_payment_link_checkout_methods_are_enabled_methods_not_a_set():
    client, captured = _client_with_captured_payload()
    client.create_payment_link(
        amount=100,
        currency="INR",
        reference_id="case:2",
        description="d",
        expire_seconds=3600,
        single_use=False,
    )
    method = captured[0]["options"]["checkout"]["method"]
    assert isinstance(method, dict)
    assert set(method) == {"netbanking", "upi", "card"}


def test_mandate_retry_uses_create_recurring_with_serializable_payload():
    """Regression: retry_mandate must go through payment.createRecurring —
    the SDK's Payment resource has no `.create`; calling it crashed at execution
    time on real keys ('Payment' object has no attribute 'create')."""
    with patch("razorpay.Client") as sdk_ctor:
        captured: list[dict] = []

        def create_recurring(data):
            captured.append(data)
            return {"id": "pay_test456", "status": "authorized"}

        sdk_ctor.return_value.payment.createRecurring.side_effect = create_recurring
        client = RealRazorpayClient("rzp_test_unittest", "secret")

    client.retry_mandate(subscription_id="sub_1", amount=500, reference_id="case:3")
    assert captured, "retry_mandate must call payment.createRecurring"
    json.dumps(captured[0])
    assert captured[0]["subscription_id"] == "sub_1"
    assert captured[0]["recurring"] == "1"


def test_transport_follows_event_provenance(monkeypatch):
    """Synthetic events execute through the simulated transport even when real
    test keys are configured; the live-webhook client uses the configured
    account. Singletons are reset around the test so no other test inherits a
    credentials-bearing client (the conftest fixture runs tests keyless)."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_unittest")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "unittest_secret")
    payments_client_module._client_singleton = None
    payments_client_module._simulated_singleton = None
    get_settings.cache_clear()
    try:
        live = get_payment_client()
        simulated = get_simulated_payment_client()
        assert not live.is_mock
        assert simulated.is_mock
        # Both accessors are singletons within their mode.
        assert get_payment_client() is live
        assert get_simulated_payment_client() is simulated
    finally:
        payments_client_module._client_singleton = None
        payments_client_module._simulated_singleton = None
        get_settings.cache_clear()
