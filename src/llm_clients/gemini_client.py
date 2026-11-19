"""Shared Gemini plumbing for every step that calls the model.

- make_client(): API key from config, timeout, SDK retries for 408 / 5xx.
  api_version="v1beta" is needed for explicit context caching
  (llm_clients.context_cache); the SDK default (v1) has no cachedContents.
- generate_json(): one structured-output call with
    * our own 429 handling (retry short rate limits, never the daily quota),
    * a check that the answer was not cut off (finish_reason must be STOP).
  Returns the raw JSON text; callers validate it with their own Pydantic model.
- generate_json_with_usage(): the same, plus token usage (Usage), including
  how many prompt tokens came from a cache. Optional cached_content: the name
  of an explicit cache that holds the system instruction.
  Every call logs its usage: "tokens prompt=... cached=... output=...".
- embed_texts(): one embed_content call (retrieval.embedding), same 429 handling.

Errors are raised as GeminiError (or google.genai.errors.APIError for
non-retryable HTTP errors such as 400 / 403 / 404).
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)


class GeminiError(Exception):
    """The model call failed or its answer is unusable."""


@dataclass(frozen=True)
class Usage:
    """Token counts of one call, from response.usage_metadata."""
    prompt_tokens: int = 0
    cached_tokens: int = 0        # part of prompt_tokens served from a cache (implicit or explicit)
    output_tokens: int = 0
    cached_content: str | None = None  # explicit cache used, if any

    @property
    def cached_ratio(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0


@dataclass(frozen=True)
class GeminiResult:
    text: str
    usage: Usage


def make_client(api_version: str | None = None) -> Any:
    if not config.GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not set; add it to src/.env")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise GeminiError("The google-genai package is not installed (pip install google-genai)") from e
    return genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(
            api_version=api_version,
            timeout=config.GEMINI_TIMEOUT_MS,
            # Without retry_options the SDK tries a sync request exactly once.
            # The SDK retries 408 / 5xx and transient connection errors. 429 is
            # left out on purpose: _generate() decides per error whether it is a
            # short rate limit (retry) or an exhausted daily quota (do not retry).
            retry_options=types.HttpRetryOptions(
                attempts=config.GEMINI_RETRY_ATTEMPTS,
                initial_delay=config.GEMINI_RETRY_INITIAL_DELAY,
                max_delay=config.GEMINI_RETRY_MAX_DELAY,
                http_status_codes=[408, 500, 502, 503, 504],
            ),
        ),
    )


def generate_json(
    client: Any,
    *,
    name: str,
    model: str,
    contents: list[Any],
    system_instruction: str,
    response_schema: dict[str, Any],
    temperature: float = 0,
) -> str:
    """Call the model for JSON output and return the response text.

    `name` labels log lines and errors (e.g. the file being processed).
    `client` may be None (a real client is created) or any object with
    `.models.generate_content(...)`, which lets tests use a fake.
    """
    return generate_json_with_usage(
        client, name=name, model=model, contents=contents, system_instruction=system_instruction,
        response_schema=response_schema, temperature=temperature,
    ).text


def generate_json_with_usage(
    client: Any,
    *,
    name: str,
    model: str,
    contents: list[Any],
    system_instruction: str | None,
    response_schema: dict[str, Any],
    temperature: float = 0,
    cached_content: str | None = None,
) -> GeminiResult:
    """generate_json() plus token usage.

    With `cached_content` (an explicit cache name), the system instruction
    lives in the cache: the API refuses a request that sets both, so
    `system_instruction` is not sent. The client must use api_version v1beta.
    """
    client = client or make_client()
    request_config: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_json_schema": response_schema,
        "temperature": temperature,
    }
    if cached_content:
        request_config["cached_content"] = cached_content
    else:
        request_config["system_instruction"] = system_instruction
    response = _generate(client, name, model=model, contents=contents, config=request_config)
    usage = _usage(response, cached_content)
    logger.info("%s: tokens prompt=%d cached=%d (%.0f%%%s) output=%d", name, usage.prompt_tokens,
                usage.cached_tokens, 100 * usage.cached_ratio,
                ", explicit cache" if cached_content else "", usage.output_tokens)
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        raise GeminiError(f"{name}: Gemini returned no answer (prompt_feedback={feedback})")
    reason = candidates[0].finish_reason
    reason_name = getattr(reason, "name", str(reason))
    if reason_name != "STOP":
        raise GeminiError(f"{name}: Gemini stopped early (finish_reason={reason_name})")
    return GeminiResult(response.text or "", usage)


def _usage(response: Any, cached_content: str | None) -> Usage:
    meta = getattr(response, "usage_metadata", None)
    def count(field: str) -> int:
        return int(getattr(meta, field, None) or 0)
    return Usage(count("prompt_token_count"), count("cached_content_token_count"),
                 count("candidates_token_count"), cached_content)


# ------------------------------------------------------------ 429 handling


@dataclass
class _RateLimit:
    daily: bool                 # daily quota exhausted: retrying cannot help
    quota_id: str | None        # e.g. GenerateRequestsPerDayPerProjectPerModel-FreeTier
    retry_delay: float | None   # server-suggested wait (RetryInfo), seconds


_DAILY_QUOTA = re.compile(r"per[_ ]?day|daily", re.IGNORECASE)
_MAX_RATE_LIMIT_WAIT = 60.0  # per-minute windows reset within a minute
_sleep = time.sleep          # replaced in tests


def _classify_429(payload: Any) -> _RateLimit:
    """Read a Gemini 429 body: is it a per-minute rate limit or the daily quota?

    Signals for "daily": error.code == "quota_exceeded" (documented string code),
    or a google.rpc.QuotaFailure violation whose quotaId / quotaMetric names a
    per-day quota. Note that daily-quota errors still carry a short RetryInfo
    delay, so the delay alone says nothing. Unknown 429s count as rate limits.
    """
    err = payload.get("error", payload) if isinstance(payload, dict) else {}
    err = err if isinstance(err, dict) else {}
    daily = str(err.get("code", "")).lower() == "quota_exceeded"
    quota_id = None
    retry_delay = None
    for detail in err.get("details") or []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for v in detail.get("violations") or []:
                if not isinstance(v, dict):
                    continue
                names = f"{v.get('quotaId', '')} {v.get('quotaMetric', '')}"
                if _DAILY_QUOTA.search(names):
                    daily, quota_id = True, v.get("quotaId") or quota_id
                else:
                    quota_id = quota_id or v.get("quotaId")
        elif kind.endswith("RetryInfo"):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
            if match:
                retry_delay = float(match.group(1))
    return _RateLimit(daily, quota_id, retry_delay)


def _generate(client: Any, name: str, **request: Any) -> Any:
    """generate_content with our own handling of 429 (see _classify_429)."""
    return call_with_rate_limit(name, lambda: client.models.generate_content(**request))


def embed_texts(
    client: Any,
    *,
    name: str,
    model: str,
    texts: list[str],
    task_type: str,
    output_dimensionality: int | None = None,
) -> list[list[float]]:
    """One embed_content call for `texts` (at most 100), same 429 handling.

    Returns one vector per text, in order. `client` may be None or a fake with
    `.models.embed_content(...)`.
    """
    client = client or make_client()
    request_config: dict[str, Any] = {"task_type": task_type}
    if output_dimensionality:
        request_config["output_dimensionality"] = output_dimensionality
    response = call_with_rate_limit(
        name,
        lambda: client.models.embed_content(model=model, contents=texts, config=request_config),
    )
    embeddings = getattr(response, "embeddings", None) or []
    if len(embeddings) != len(texts):
        raise GeminiError(f"{name}: Gemini returned {len(embeddings)} embeddings for {len(texts)} texts")
    return [list(e.values) for e in embeddings]


def call_with_rate_limit(name: str, call: Any) -> Any:
    """Run `call()`; retry short 429 rate limits, never the daily quota."""
    attempts = max(config.GEMINI_RETRY_ATTEMPTS, 1)
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as e:  # google.genai.errors.APIError, duck-typed
            if getattr(e, "code", None) != 429:
                raise
            limit = _classify_429(getattr(e, "details", None))
            if limit.daily:
                raise GeminiError(
                    f"{name}: Gemini daily quota exhausted "
                    f"({limit.quota_id or 'quota_exceeded'}); not retrying. "
                    "Run again after the quota resets"
                ) from e
            if attempt == attempts:
                raise GeminiError(
                    f"{name}: Gemini rate limit (429) persisted after {attempts} attempts"
                ) from e
            backoff = config.GEMINI_RETRY_INITIAL_DELAY * 2 ** (attempt - 1) + random.uniform(0, 1)
            wait = min(limit.retry_delay if limit.retry_delay is not None else backoff,
                       _MAX_RATE_LIMIT_WAIT)
            logger.warning(
                "%s: Gemini rate limit (429%s), retrying in %.1fs (attempt %d/%d)",
                name, f", {limit.quota_id}" if limit.quota_id else "", wait, attempt + 1, attempts,
            )
            _sleep(wait)
    raise AssertionError("unreachable")
