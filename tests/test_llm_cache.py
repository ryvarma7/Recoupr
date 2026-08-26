"""LLM response cache + rate-limit backoff (app/agents/llm.py).

The cache is transport-level memoization: identical requests must be answered
without an HTTP call, persisted across client instances via llmcacheentry.
429s back off per Retry-After before raising LLMError — fail-safe preserved.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlmodel import Session, select

from app.agents.llm import (
    _CACHE_STATS,
    _MEMORY_CACHE,
    RATE_LIMIT_ATTEMPTS,
    LLMClient,
    LLMError,
    _rate_limit_wait,
    llm_cache_stats,
)
from app.core.config import Settings
from app.db.session import get_session
from app.models.entities import LLMCacheEntry

SYSTEM = "You classify why an Indian digital payment failed."
PROMPT = "Event: payment.failed. Error code: internal_error."


class Verdict(BaseModel):
    category: str
    confidence: float


GOOD_BODY = {
    "choices": [{"message": {"content": '{"category": "bank_issue", "confidence": 0.7}'}}]
}


class FakeResponse:
    def __init__(self, status_code, body=None, text="", headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeHttp:
    """Stands in for httpx.Client; replays queued responses and counts calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _fresh_memory_cache():
    _MEMORY_CACHE.clear()
    # Stats are process-global; zero them so assertions see per-test counts.
    _CACHE_STATS.update(hits=0, misses=0, stores=0)
    yield
    _MEMORY_CACHE.clear()


@pytest.fixture()
def client() -> LLMClient:
    # Init-arg key beats the hermetic env pins; the fake transport below absorbs
    # every call, so nothing ever reaches the network.
    return LLMClient(settings=Settings(groq_api_key="test-key"))


@pytest.fixture()
def db_backed(engine):
    """Point the cache's DB tier at the test engine, mirroring app wiring."""
    get_session._engine = engine  # type: ignore[attr-defined]
    yield engine
    del get_session._engine  # type: ignore[attr-defined]


def _install(client: LLMClient, responses) -> FakeHttp:
    fake = FakeHttp(responses)
    client._ensure_client = lambda: fake  # type: ignore[method-assign]
    return fake


def test_first_call_is_a_miss_and_stores(client):
    fake = _install(client, [FakeResponse(200, GOOD_BODY)])
    verdict = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)

    assert verdict.category == "bank_issue"
    assert fake.calls == 1
    assert llm_cache_stats() == {"hits": 0, "misses": 1, "stores": 1}


def test_repeat_call_served_from_memory_without_http(client):
    _install(client, [FakeResponse(200, GOOD_BODY)])
    first = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)

    fake = _install(client, [])  # any post here would IndexError the empty queue
    second = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)

    assert second == first
    assert fake.calls == 0
    assert llm_cache_stats()["hits"] == 1


def test_cache_survives_a_fresh_client_via_db(db_backed, client):
    _install(client, [FakeResponse(200, GOOD_BODY)])
    first = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)
    assert _MEMORY_CACHE, "store should populate the memory tier"

    _MEMORY_CACHE.clear()
    fresh = LLMClient(settings=Settings(groq_api_key="test-key"))
    fake = _install(fresh, [])
    second = fresh.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)

    assert second == first          # served from the llmcacheentry row…
    assert fake.calls == 0          # …without an HTTP call
    assert llm_cache_stats()["hits"] == 1


def test_different_prompt_is_a_different_key(client):
    _install(client, [
        FakeResponse(200, GOOD_BODY),
        FakeResponse(200, {"choices": [{"message": {
            "content": '{"category": "insufficient_funds", "confidence": 0.8}'}}]}),
    ])
    a = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)
    b = client.classify(system=SYSTEM, prompt=PROMPT + " Amount differs.", schema=Verdict)

    assert a.category != b.category
    assert llm_cache_stats()["misses"] == 2


def test_corrupt_cache_row_falls_through_to_live_call(db_backed, client):
    _install(client, [FakeResponse(200, GOOD_BODY)])
    first = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)
    assert first.category == "bank_issue"

    # Corrupt the persisted row in place, as an evolved schema or manual edit would.
    with Session(db_backed) as session:
        rows = session.exec(select(LLMCacheEntry)).all()
        assert len(rows) == 1
        rows[0].response_json = '{"category": 12345}'  # wrong type for Verdict
        session.add(rows[0])
        session.commit()

    _MEMORY_CACHE.clear()
    fake = _install(client, [FakeResponse(200, GOOD_BODY)])

    verdict = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)

    assert verdict.category == "bank_issue"   # recomputed live…
    assert fake.calls == 1                    # …instead of raising ValidationError


def test_429_retries_on_retry_after_then_succeeds(client, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.agents.llm.time.sleep", lambda s: sleeps.append(s))
    fake = _install(client, [
        FakeResponse(429, text="Rate limit reached", headers={"Retry-After": "0"}),
        FakeResponse(200, GOOD_BODY),
    ])

    verdict = client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)

    assert verdict.category == "bank_issue"
    assert fake.calls == 2
    assert sleeps == [0.0]


def test_429_exhausted_raises_llm_error_fail_safe(client, monkeypatch):
    monkeypatch.setattr("app.agents.llm.time.sleep", lambda s: None)
    fake = _install(client, [FakeResponse(429, text="TPM limit")] * RATE_LIMIT_ATTEMPTS)

    with pytest.raises(LLMError, match="after 3 attempts"):
        client.classify(system=SYSTEM, prompt=PROMPT, schema=Verdict)
    assert fake.calls == RATE_LIMIT_ATTEMPTS


def test_rate_limit_wait_prefers_header_caps_at_max():
    long = FakeResponse(429, headers={"Retry-After": "120"})
    assert _rate_limit_wait(long) == 30.0

    hinted = FakeResponse(429, text="Please try again in 12.5s.")
    assert _rate_limit_wait(hinted) == 12.5

    bare = FakeResponse(429)
    assert _rate_limit_wait(bare) > 0
