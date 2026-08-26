"""Central configuration.

Every subsystem degrades gracefully to a clearly-logged demo mode when its real
credential is absent, and picks the real thing up automatically once present —
mock-vs-real is an internal branch inside client wrappers, never two code paths.
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database — unset falls back to a local SQLite file (zero-setup dev/demo).
    database_url: str = "sqlite:///./recoupr.db"

    # Razorpay — empty means mock mode (test-mode-shaped fake responses).
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Groq (OpenAI-compatible) — empty means LLM_DISABLED (rule-only diagnosis,
    # template decisions). Model is a free-tier chat model; see README.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    # Simulated DLT / WhatsApp Business sender registration.
    sms_sender_verified: bool = False
    whatsapp_sender_verified: bool = False

    # Embedded maintenance scheduler (TTL sweep + deferred requeue). Set
    # RECOUPR_SCHEDULER_ENABLED=false to run the /maintenance/tick endpoint from
    # cron instead.
    scheduler_enabled: bool = True
    maintenance_interval_seconds: int = 300

    merchant_timezone: str = "Asia/Kolkata"

    @property
    def razorpay_mock(self) -> bool:
        return not (self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def webhook_secret_bypassed(self) -> bool:
        return self.razorpay_mock and not self.razorpay_webhook_secret

    @property
    def llm_disabled(self) -> bool:
        return not self.groq_api_key

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.merchant_timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def subsystem_modes(settings: Settings) -> dict[str, str]:
    """Human-readable mode labels, logged loudly at startup."""
    return {
        "database": "postgres" if settings.database_url.startswith("postgresql") else "sqlite-fallback",
        "razorpay": "MOCK" if settings.razorpay_mock else "real-test-mode",
        "webhook_signature": (
            "BYPASSED (no secret configured; mock mode only)" if settings.webhook_secret_bypassed else "enforced"
        ),
        "llm": (
            "LLM_DISABLED (rule-only diagnosis, template decisions)"
            if settings.llm_disabled
            else f"groq:{settings.groq_model}"
        ),
        "sms_sender": "verified" if settings.sms_sender_verified else "UNVERIFIED (channel blocked by gate)",
        "whatsapp_sender": "verified" if settings.whatsapp_sender_verified else "UNVERIFIED (channel blocked by gate)",
    }
