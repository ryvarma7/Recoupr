"""Time handling.

The whole pipeline takes `now` as an explicit parameter instead of reading the
wall clock internally. Production callers pass SystemClock.now(); the batch
simulator passes synthetic timeline timestamps, which lets a 14-day case TTL or
a 6-hour retry cooldown elapse in milliseconds of wall time without any hidden
global state or scaled constants inside business logic.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_naive_utc(dt: datetime) -> datetime:
    """Normalize any datetime to naive UTC — the store-and-compare convention.

    The database round-trips datetimes without tzinfo (SQLite drops it; the
    batch simulator writes naive local-UTC values), so every arithmetic and
    comparison inside the pipeline happens on naive UTC. Callers may pass aware
    or naive `now` freely; this is the single choke point that makes both safe.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    """Inverse view for timezone math: naive values are interpreted as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()
