"""Policy snapshot helpers + quiet-hours evaluation.

The gate never reads the live GuardrailPolicy table — it reads only the
Case.policy_snapshot dict captured at case creation, so a later policy edit can
never change an in-flight case's behavior.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.clock import as_utc


class PolicySnapshot:
    """Typed read-only view over a Case.policy_snapshot JSON dict."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def max_retries_per_case(self) -> int:
        return int(self._data["max_retries_per_case"])

    @property
    def retry_cooldown_hours(self) -> int:
        return int(self._data["retry_cooldown_hours"])

    @property
    def message_cap_per_case(self) -> int:
        return int(self._data["message_cap_per_case"])

    @property
    def message_cap_window_days(self) -> int:
        return int(self._data["message_cap_window_days"])

    @property
    def allowed_channels(self) -> list[str]:
        return list(self._data.get("allowed_channels", []))

    @property
    def consent_required_channels(self) -> list[str]:
        return list(self._data.get("consent_required_channels", []))

    @property
    def amount_immutability(self) -> bool:
        return bool(self._data.get("amount_immutability", True))

    @property
    def case_ttl_days(self) -> int:
        return int(self._data["case_ttl_days"])

    @property
    def payment_link_expiry_hours(self) -> int:
        return int(self._data["payment_link_expiry_hours"])

    @property
    def payment_link_single_use(self) -> bool:
        return bool(self._data.get("payment_link_single_use", True))

    @property
    def quiet_hours_start(self) -> time:
        return time.fromisoformat(self._data["quiet_hours_start"])

    @property
    def quiet_hours_end(self) -> time:
        return time.fromisoformat(self._data["quiet_hours_end"])


def in_quiet_hours(now: datetime, policy: PolicySnapshot, tz: ZoneInfo) -> bool:
    """True when `now` falls inside [start, end) evaluated in the merchant timezone.

    Handles windows crossing midnight (21:00 → 08:00): a local time is quiet when
    local >= start OR local < end. Naive inputs are interpreted as UTC.
    """
    local = as_utc(now).astimezone(tz).time()
    start, end = policy.quiet_hours_start, policy.quiet_hours_end
    if start == end:
        return False
    if start < end:  # e.g. 13:00–15:00
        return start <= local < end
    return local >= start or local < end  # crosses midnight


def quiet_hours_resume_utc(now: datetime, policy: PolicySnapshot, tz: ZoneInfo) -> datetime | None:
    """Naive-UTC instant when messaging may resume; None when not currently quiet.

    Used by the deferral path: a timing-only gate block should reschedule the
    case to this instant instead of burning a human escalation on "it's 2 a.m."
    """
    start, end = policy.quiet_hours_start, policy.quiet_hours_end
    if start == end or not in_quiet_hours(now, policy, tz):
        return None
    local_now = as_utc(now).astimezone(tz)
    for day_offset in (0, 1):
        candidate_local = local_now.replace(
            hour=end.hour, minute=end.minute, second=0, microsecond=0,
        ) + timedelta(days=day_offset)
        if candidate_local > local_now:
            return candidate_local.astimezone(timezone.utc).replace(tzinfo=None)
    return None  # unreachable for any sane window
