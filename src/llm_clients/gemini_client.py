"""Shared Gemini plumbing for every step that calls the model.

- make_client(): API key from config, timeout, SDK retries for 408 / 5xx.
- generate_json(): one structured-output call with
    * our own 429 handling (retry short rate limits, never the daily quota),
    * a check that the answer was not cut off (finish_reason must be STOP).
  Returns the raw JSON text; callers validate it with their own Pydantic model.

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


def make_client() -> Any:
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
    client = client or make_client()
    response = _generate(
        client,
        name,
        model=model,
        contents=contents,
        config={
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "response_json_schema": response_schema,
            "temperature": temperature,
        },
    )
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        raise GeminiError(f"{name}: Gemini returned no answer (prompt_feedback={feedback})")
    reason = candidates[0].finish_reason
    reason_name = getattr(reason, "name", str(reason))
    if reason_name != "STOP":
        raise GeminiError(f"{name}: Gemini stopped early (finish_reason={reason_name})")
    return response.text or ""


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
    attempts = max(config.GEMINI_RETRY_ATTEMPTS, 1)
    for attempt in range(1, attempts + 1):
        try:
            return client.models.generate_content(**request)
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
