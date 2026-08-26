"""Groq chat-completions wrapper (OpenAI-compatible endpoint, httpx only).

Design constraints from the spec:
- Absent key → LLM_DISABLED mode (callers branch on `.available`; nothing here calls out).
- Timeout / connection failure → raised as LLMTimedOut so the pipeline can fail safe
  (escalate_human) instead of failing open. HTTP errors, malformed bodies and schema
  violations raise LLMError — same fail-safe contract.
- Structured output via response_format={"type": "json_object"} plus the JSON schema
  embedded in the system message, validated against the caller's Pydantic model.
  Groq rejects json_object mode unless the word "json" appears in the messages,
  so classify() always appends an explicit JSON instruction.
- The model never receives tools and never executes anything — it returns data only.
- temperature=0 for reproducibility; the guardrail gate remains the safety boundary.
- Responses are memoized (memory tier + persistent llmcacheentry table): identical
  requests are answered from cache without spending free-tier TPM quota. A 429 backs
  off per Retry-After before raising LLMError — still fail-safe, never fail-open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time

import httpx
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.entities import LLMCacheEntry

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "openai/gpt-oss-20b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CLASSIFY_TIMEOUT_SECONDS = 8.0
CLASSIFY_MAX_TOKENS = 512
RATE_LIMIT_ATTEMPTS = 3         # total tries (incl. the first) before failing safe
RATE_LIMIT_MAX_WAIT = 30.0      # cap on a server-suggested backoff, seconds
RATE_LIMIT_DEFAULT_WAIT = 20.0  # when the response carries no Retry-After hint

# First-tier cache: process-lifetime, populated from the DB table on first touch.
# Keyed by the sha256 request key below; values are validated-model JSON strings.
_MEMORY_CACHE: dict[str, str] = {}
_CACHE_STATS = {"hits": 0, "misses": 0, "stores": 0}

_RETRY_AFTER_HINT = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)


class LLMTimedOut(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def _cache_key(model_id: str, system_text: str, prompt: str) -> str:
    """Hash exactly what would go over the wire (system already embeds the schema)."""
    payload = json.dumps(
        {"model": model_id, "system": system_text, "prompt": prompt},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def llm_cache_stats() -> dict[str, int]:
    """Hit/miss/store counters for this process — observability, not case state."""
    return dict(_CACHE_STATS)


def _cache_db_engine():
    """Reuse the app's engine once it exists; stay memory-only before that.

    Deliberately does NOT create an engine: tests run without one, and opening a
    database connection from inside an LLM call must never become a side effect.
    """
    return getattr(get_session, "_engine", None)


def _cache_get(key: str) -> str | None:
    if key in _MEMORY_CACHE:
        _CACHE_STATS["hits"] += 1
        return _MEMORY_CACHE[key]
    engine = _cache_db_engine()
    if engine is None:
        return None
    try:
        with Session(engine) as session:
            row = session.get(LLMCacheEntry, key)
            if row is None:
                return None
            _MEMORY_CACHE[key] = row.response_json
            _CACHE_STATS["hits"] += 1
            return row.response_json
    except Exception:  # cache must never break classification — degrade to miss
        logger.debug("llm cache read failed (non-fatal)", exc_info=True)
        return None


def _cache_put(key: str, model_id: str, schema_name: str, response_json: str) -> None:
    _MEMORY_CACHE[key] = response_json
    _CACHE_STATS["stores"] += 1  # the memory tier already serves hits
    engine = _cache_db_engine()
    if engine is None:
        return
    try:
        with Session(engine) as session:
            session.merge(
                LLMCacheEntry(
                    key=key,
                    model=model_id,
                    schema_name=schema_name,
                    response_json=response_json,
                )
            )
            session.commit()
        _CACHE_STATS["stores"] += 1
    except Exception:  # memory tier already holds it; persistence is best-effort
        logger.debug("llm cache write failed (non-fatal)", exc_info=True)


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        # Settings are re-read live so env changes (and test isolation fixtures)
        # take effect immediately; only the HTTP client itself is cached.
        self._initial = settings
        self._client: httpx.Client | None = None

    @property
    def available(self) -> bool:
        return not (self._initial or get_settings()).llm_disabled

    @property
    def model_id(self) -> str:
        return (self._initial or get_settings()).groq_model or DEFAULT_MODEL_ID

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=CLASSIFY_TIMEOUT_SECONDS)
        return self._client

    def classify(self, *, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
        """One structured classification call. Raises LLMTimedOut / LLMError."""
        key = (self._initial or get_settings()).groq_api_key
        if not key:
            raise RuntimeError("LLMClient used while disabled — check .available first")

        system_text = (
            f"{system}\n\n"
            "Respond with a single valid JSON object and nothing else.\n"
            f"The JSON object must match this JSON schema: {json.dumps(schema.model_json_schema())}"
        )

        cache_key = _cache_key(self.model_id, system_text, prompt)
        cached = _cache_get(cache_key)
        if cached is not None:
            try:
                logger.info("llm cache hit %s… (%s)", cache_key[:12], schema.__name__)
                return schema.model_validate_json(cached)
            except ValueError:  # stored row no longer matches an evolved schema
                logger.warning(
                    "llm cache entry %s… failed validation; recomputing", cache_key[:12]
                )
        _CACHE_STATS["misses"] += 1

        response = self._post_with_backoff(key, system_text, prompt)

        if response.status_code != 200:
            raise LLMError(f"llm status {response.status_code}: {response.text[:200]}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
            validated = schema.model_validate(json.loads(content))
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"llm returned unparseable output: {exc}") from exc

        _cache_put(cache_key, self.model_id, schema.__name__, validated.model_dump_json())
        return validated

    def _post_with_backoff(self, key: str, system_text: str, prompt: str) -> httpx.Response:
        """POST once per attempt; on 429 sleep out the suggested window and retry.

        Exhausting the attempts raises LLMError like any other transport failure —
        callers keep their fail-safe escalation path unchanged.
        """
        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
            try:
                response = self._ensure_client().post(
                    f"{GROQ_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model_id,
                        "temperature": 0,
                        "max_tokens": CLASSIFY_MAX_TOKENS,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_text},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                raise LLMTimedOut(f"llm call did not return in time: {exc}") from exc

            if response.status_code != 429:
                return response
            if attempt == RATE_LIMIT_ATTEMPTS:
                raise LLMError(
                    f"llm rate limited after {attempt} attempts: {response.text[:200]}"
                )
            wait = _rate_limit_wait(response)
            logger.warning(
                "llm rate limited (TPM); backing off %.1fs (attempt %d/%d)",
                wait,
                attempt,
                RATE_LIMIT_ATTEMPTS,
            )
            time.sleep(wait)
        raise LLMError("unreachable")  # loop returns or raises on every path


def _rate_limit_wait(response: httpx.Response) -> float:
    """Best-effort backoff: Retry-After header, Groq's 'try again in Ns' text, default."""
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return min(float(header), RATE_LIMIT_MAX_WAIT)
        except ValueError:
            pass
    match = _RETRY_AFTER_HINT.search(response.text)
    if match:
        return min(float(match.group(1)), RATE_LIMIT_MAX_WAIT)
    return RATE_LIMIT_DEFAULT_WAIT


_llm_singleton: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = LLMClient()
    return _llm_singleton
