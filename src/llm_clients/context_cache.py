"""Explicit Gemini context cache for a stable prompt prefix.

The SRS generator's system instruction (SYSTEM_PROMPT + core context, about
7.7k tokens) is identical for every call with the same core context version.
It is stored once as a Gemini cachedContent and referenced by name; cached
tokens are billed at a reduced rate.

Identity: display_name = "spectest-<core version>-<sha256(system instruction)[:8]>"
plus the model (a cache belongs to one model). A new core context version, an
edited SYSTEM_PROMPT or another model gives another cache; nothing stale is
ever reused.

Lifetime: created with TTL GEMINI_CACHE_TTL_SECONDS. Reusing a cache extends
its TTL by the same amount, so a series of runs keeps it alive and it expires
by itself afterwards (storage is billed per hour while it exists).

Facts checked against the API (gemini-3.8-flash, 2026-11):
  - cachedContents exists only in v1beta (v1: 404 on list / create);
  - a cache may hold only a system_instruction (no contents);
  - a request using cached_content must not set system_instruction, tools or
    tool_config (400 INVALID_ARGUMENT);
  - using an expired / deleted cache: 403 "CachedContent not found".

    python -m llm_clients.context_cache --list    # this project's caches
    python -m llm_clients.context_cache --clear   # delete them
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)

PREFIX = "spectest-"
_MIN_REMAINING = dt.timedelta(seconds=60)  # do not reuse a cache about to expire


class ContextCacheError(Exception):
    """The cache could not be found, created or extended."""


@dataclass(frozen=True)
class CacheHandle:
    name: str              # cachedContents/... (pass as cached_content)
    display_name: str
    model: str
    created: bool          # True: created now; False: an existing cache was reused
    tokens: int | None = None
    expire_time: dt.datetime | None = None


def display_name(version: str, system_instruction: str) -> str:
    digest = hashlib.sha256(system_instruction.encode("utf-8")).hexdigest()[:8]
    return f"{PREFIX}{version}-{digest}"


def _same_model(a: str | None, b: str) -> bool:
    return (a or "").removeprefix("models/") == b.removeprefix("models/")


def get_or_create(
    client: Any,
    *,
    model: str,
    system_instruction: str,
    version: str,
    ttl_seconds: int | None = None,
) -> CacheHandle:
    """Reuse the live cache for (model, system instruction, version), or create it."""
    from google.genai import types

    ttl = f"{ttl_seconds or config.GEMINI_CACHE_TTL_SECONDS}s"
    wanted = display_name(version, system_instruction)
    now = dt.datetime.now(dt.timezone.utc)
    try:
        live = [
            c for c in client.caches.list()
            if c.display_name == wanted and _same_model(c.model, model)
            and c.expire_time is not None and c.expire_time - now > _MIN_REMAINING
        ]
        if live:
            best = max(live, key=lambda c: c.expire_time)
            extended = client.caches.update(name=best.name, config=types.UpdateCachedContentConfig(ttl=ttl))
            logger.info("context cache reused: %s (%s), expires %s", wanted, best.name, extended.expire_time)
            return CacheHandle(best.name, wanted, model, False, _tokens(best), extended.expire_time)
        cache = client.caches.create(model=model, config=types.CreateCachedContentConfig(
            display_name=wanted, system_instruction=system_instruction, ttl=ttl,
        ))
    except Exception as e:  # APIError (404 on v1, quota, model without caching), network
        raise ContextCacheError(f"{type(e).__name__}: {e}") from e
    logger.info("context cache created: %s (%s, %s tokens), expires %s",
                wanted, cache.name, _tokens(cache), cache.expire_time)
    return CacheHandle(cache.name, wanted, model, True, _tokens(cache), cache.expire_time)


def _tokens(cache: Any) -> int | None:
    meta = getattr(cache, "usage_metadata", None)
    return getattr(meta, "total_token_count", None)


def is_cache_missing(error: BaseException) -> bool:
    """True for the API error of a request whose cache expired or was deleted."""
    return getattr(error, "code", None) in (403, 404) and "cachedcontent" in str(error).lower()


def list_caches(client: Any) -> list[Any]:
    return [c for c in client.caches.list() if (c.display_name or "").startswith(PREFIX)]


def clear(client: Any) -> int:
    caches = list_caches(client)
    for c in caches:
        client.caches.delete(name=c.name)
    return len(caches)


if __name__ == "__main__":
    import argparse

    from llm_clients import gemini_client

    from app_logging import setup_cli_logging

    setup_cli_logging("context_cache", quiet_http=True)
    parser = argparse.ArgumentParser(prog="python -m llm_clients.context_cache",
                                     description="List or delete SpecTestAI Gemini context caches.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true")
    action.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    api = gemini_client.make_client(api_version=config.GEMINI_CACHE_API_VERSION)
    if args.list:
        caches = list_caches(api)
        for c in caches:
            print(f"{c.display_name}  {c.model}  {_tokens(c)} tokens  expires {c.expire_time}  {c.name}")
        print(f"{len(caches)} cache(s)")
    else:
        print(f"deleted {clear(api)} cache(s)")
