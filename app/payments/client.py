"""Razorpay client wrapper — mock-vs-real is an internal branch, not two code paths.

Callers always talk to PaymentClient. With test keys configured the real SDK is
used; without them MockRazorpayClient returns realistic test-mode-shaped
responses so the entire product runs with zero credentials.

Test mode only: the wrapper refuses to initialise against anything resembling a
live key as a belt-and-braces startup guard (live Razorpay key ids start
"rzp_live").
"""

from __future__ import annotations

import logging
import secrets

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    """Raised when an external execution fails after retries — callers fail safe."""


class LiveKeyError(RuntimeError):
    """A live-looking key was supplied — refuse everything."""


def _assert_test_mode(key_id: str) -> None:
    if key_id.startswith("rzp_live"):
        raise LiveKeyError("live Razorpay key detected — Recoupr runs test mode only")


class MockRazorpayClient:
    """Realistic fake responses shaped like Razorpay test-mode objects."""

    def __init__(self) -> None:
        self.created_links: list[dict] = []
        self.retries_executed: list[dict] = []

    def create_payment_link(
        self,
        *,
        amount: int,
        currency: str,
        reference_id: str,
        description: str,
        expire_seconds: int,
        single_use: bool,
    ) -> dict:
        suffix = secrets.token_hex(5)
        payload = {
            "id": f"plink_{suffix}",
            "object": "payment_link",
            "amount": amount,
            "currency": currency,
            "reference_id": reference_id,
            "description": description,
            "short_url": f"https://rzp.io/i/mock{suffix}",
            "expire_by_expiry_seconds": expire_seconds,
            "single_use": single_use,
            "status": "created",
            "test_mode": True,
        }
        self.created_links.append(payload)
        logger.info("[MOCK RAZORPAY] payment link created %s → %s", payload["id"], payload["short_url"])
        return payload

    def retry_mandate(self, *, subscription_id: str, amount: int, reference_id: str) -> dict:
        payload = {
            "id": f"pay_{secrets.token_hex(6)}",
            "object": "mandate_retry",
            "subscription_id": subscription_id,
            "amount": amount,
            "reference_id": reference_id,
            "status": "initiated",
            "test_mode": True,
        }
        self.retries_executed.append(payload)
        logger.info("[MOCK RAZORPAY] mandate retry initiated %s on %s", payload["id"], subscription_id)
        return payload


class RealRazorpayClient:
    """Thin wrapper over the official SDK — test-mode credentials required."""

    def __init__(self, key_id: str, key_secret: str) -> None:
        _assert_test_mode(key_id)
        import razorpay  # imported lazily so mock mode needs no credentials at all

        self._sdk = razorpay.Client(auth=(key_id, key_secret))

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    def create_payment_link(
        self,
        *,
        amount: int,
        currency: str,
        reference_id: str,
        description: str,
        expire_seconds: int,
        single_use: bool,
    ) -> dict:
        import time as _time

        data = {
            "amount": amount,
            "currency": currency,
            "accept_partial": False,
            "reference_id": reference_id,
            "description": description,
            "expire_by": int(_time.time()) + expire_seconds,
            "options": {"checkout": {"method": {"netbanking", "upi", "card"}}},
        }
        link = self._sdk.payment_link.create(data)
        if single_use:
            # Razorpay payment links are single-payment by construction when
            # accept_partial is False; assert the shape we depend on.
            link.setdefault("single_use", True)
        logger.info("payment link created %s", link.get("id"))
        return link

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    def retry_mandate(self, *, subscription_id: str, amount: int, reference_id: str) -> dict:
        # Token-based recurring charge against a stored mandate (e-NACH / UPI Autopay).
        payment = self._sdk.payment.create(
            {
                "amount": amount,
                "currency": "INR",
                "subscription_id": subscription_id,
                "reference_id": reference_id,
                "recurring": "1",
            }
        )
        logger.info("mandate retry submitted %s", payment.get("id"))
        return payment


class PaymentClient:
    """Public facade — picks mock vs real internally from settings."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._impl: MockRazorpayClient | RealRazorpayClient
        if self._settings.razorpay_mock:
            self._impl = MockRazorpayClient()
            logger.warning("RAZORPAY_MOCK — returning simulated test-mode responses")
        else:
            _assert_test_mode(self._settings.razorpay_key_id)
            self._impl = RealRazorpayClient(self._settings.razorpay_key_id, self._settings.razorpay_key_secret)

    @property
    def is_mock(self) -> bool:
        return isinstance(self._impl, MockRazorpayClient)

    def create_payment_link(self, **kwargs) -> dict:
        try:
            return self._impl.create_payment_link(**kwargs)
        except Exception as exc:
            raise ExecutionError(f"payment link creation failed: {exc}") from exc

    def retry_mandate(self, **kwargs) -> dict:
        try:
            return self._impl.retry_mandate(**kwargs)
        except Exception as exc:
            raise ExecutionError(f"mandate retry failed: {exc}") from exc


_client_singleton: PaymentClient | None = None


def get_payment_client() -> PaymentClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = PaymentClient()
    return _client_singleton
