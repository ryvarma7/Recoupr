"""Thin Anthropic SDK wrapper.

Design constraints from the spec:
- Absent key → LLM_DISABLED mode (callers branch on `.available`; nothing here calls out).
- Timeout / connection failure → raised as LLMTimedOut so the pipeline can fail safe
  (escalate_human) instead of failing open.
- Structured output via client.messages.parse() with a Pydantic schema; the model
  never receives tools and never executes anything — it returns data only.
- Note on temperature: Claude Sonnet 5 removed sampling parameters (sending one is
  a 400). Determinism comes from effort control (`output_config.effort`) plus the
  guardrail gate being the actual safety boundary.
"""

from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

MODEL_ID = "claude-sonnet-5"
CLASSIFY_TIMEOUT_SECONDS = 8.0
CLASSIFY_MAX_TOKENS = 512


class LLMTimedOut(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        # Settings are re-read live so env changes (and test isolation fixtures)
        # take effect immediately; only the HTTP client itself is cached.
        self._initial = settings
        self._client: anthropic.Anthropic | None = None

    @property
    def available(self) -> bool:
        return not (self._initial or get_settings()).llm_disabled

    @property
    def model_id(self) -> str:
        return MODEL_ID

    def _ensure_client(self) -> anthropic.Anthropic:
        if self._client is None:
            key = (self._initial or get_settings()).anthropic_api_key
            if not key:
                raise RuntimeError("LLMClient used while disabled — check .available first")
            self._client = anthropic.Anthropic(
                api_key=key,
                timeout=CLASSIFY_TIMEOUT_SECONDS,
                max_retries=0,  # we fail safe immediately rather than burn wall-clock retrying
            )
        return self._client

    def classify(self, *, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
        """One structured classification call. Raises LLMTimedOut / LLMError."""
        try:
            response = self._ensure_client().messages.parse(
                model=MODEL_ID,
                max_tokens=CLASSIFY_MAX_TOKENS,
                output_config={"effort": "low"},
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise LLMTimedOut(f"llm call did not return in time: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(f"llm rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"llm status {exc.status_code}: {exc.message}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("llm returned no structured output")
        return parsed


_llm_singleton: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = LLMClient()
    return _llm_singleton
