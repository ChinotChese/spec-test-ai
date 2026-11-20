"""Project settings. Paths are anchored on this file, not the working directory.

Values can be overridden by environment variables or by src/.env
(KEY=VALUE lines; real environment variables win over .env).
"""

from __future__ import annotations

import os
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE per line, # comments, optional quotes."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(SRC_DIR / ".env")

INPUT_DIR = Path(os.getenv("SPECTEST_INPUT_DIR", SRC_DIR / "input"))
OUTPUT_DIR = Path(os.getenv("SPECTEST_OUTPUT_DIR", SRC_DIR / "output"))   # exported SRS files
DATA_DIR = Path(os.getenv("SPECTEST_DATA_DIR", SRC_DIR / "data"))         # app state, gitignored
DB_PATH = Path(os.getenv("SPECTEST_DB_PATH", DATA_DIR / "spectest.db"))

# CLI log files (app_logging): <LOG_DIR>/spectest-YYYY-MM-DD.log. "off" or empty disables.
_log_dir = os.getenv("SPECTEST_LOG_DIR", str(SRC_DIR / "logs")).strip()
LOG_DIR: Path | None = None if _log_dir.lower() in ("", "off", "none", "0", "false") else Path(_log_dir)
LOG_LEVEL = os.getenv("SPECTEST_LOG_LEVEL", "INFO")  # file only; the console stays at INFO

# Gemini. GEMINI_API_KEY is passed to the client explicitly (GOOGLE_API_KEY is not read).
GEMINI_MODEL_LOADER = os.getenv("GEMINI_MODEL_LOADER", "gemini-3.5-flash-lite")
GEMINI_MODEL_EXTRACTOR = os.getenv("GEMINI_MODEL_EXTRACTOR", "gemini-3.8-flash")
GEMINI_MODEL_GENERATOR = os.getenv("GEMINI_MODEL_GENERATOR", "gemini-3.8-flash")
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "300000"))
# Retries on 408 / 429 / 5xx and transient network errors, exponential backoff.
GEMINI_RETRY_ATTEMPTS = int(os.getenv("GEMINI_RETRY_ATTEMPTS", "4"))      # total tries, 1 = no retry
GEMINI_RETRY_INITIAL_DELAY = float(os.getenv("GEMINI_RETRY_INITIAL_DELAY", "2"))  # seconds
GEMINI_RETRY_MAX_DELAY = float(os.getenv("GEMINI_RETRY_MAX_DELAY", "30"))          # seconds
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Embeddings (retrieval.embedding). gemini-embedding-001 returns 3072 dims by
# default; 768 / 1536 are the recommended smaller sizes. Changing the model or
# the size needs a new (or dropped) collection: the vector size is fixed there.
GEMINI_MODEL_EMBEDDING = os.getenv("GEMINI_MODEL_EMBEDDING", "gemini-embedding-001")
GEMINI_EMBEDDING_DIM = int(os.getenv("GEMINI_EMBEDDING_DIM", "1536"))
GEMINI_EMBEDDING_BATCH_SIZE = int(os.getenv("GEMINI_EMBEDDING_BATCH_SIZE", "100"))  # API max 100


def _optional_bool(name: str) -> bool | None:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return None
    return value in ("1", "true", "yes", "on")


# Qdrant (retrieval.qdrant_store). QDRANT_HOST may also be a full URL
# (http://host:6333). QDRANT_HTTPS unset: the client uses https when an API key
# is set; set QDRANT_HTTPS=false for a plain-http server with an API key.
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
QDRANT_HTTPS = _optional_bool("QDRANT_HTTPS")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "source_passages")
QDRANT_TIMEOUT = int(os.getenv("QDRANT_TIMEOUT", "30"))  # seconds

# SRS generator evidence (agents.srs_generator): passages cited by the approved
# requirements, plus passages retrieved (retrieval.retriever) for each narrative
# section and each feature. TOP_K per query; MAX caps the retrieved extras
# (cited passages are always sent and do not count).
SRS_RETRIEVAL_MODE = os.getenv("SRS_RETRIEVAL_MODE", "hybrid")  # hybrid / dense / sparse
SRS_RETRIEVAL_TOP_K = int(os.getenv("SRS_RETRIEVAL_TOP_K", "4"))
SRS_RETRIEVAL_MAX_PASSAGES = int(os.getenv("SRS_RETRIEVAL_MAX_PASSAGES", "40"))

# Context cache for the SRS generator's stable prefix (system prompt + core
# context), see llm_clients.context_cache.
#   explicit  create / reuse a Gemini cache named after the core context version
#             (needs the v1beta API); falls back to the full prompt on failure
#   off       always send the full prompt (Gemini implicit caching may still hit)
# Cached tokens of every call are logged and reported either way.
GEMINI_CONTEXT_CACHE = os.getenv("GEMINI_CONTEXT_CACHE", "explicit")
GEMINI_CACHE_TTL_SECONDS = int(os.getenv("GEMINI_CACHE_TTL_SECONDS", "600"))
GEMINI_CACHE_API_VERSION = os.getenv("GEMINI_CACHE_API_VERSION", "v1beta")
