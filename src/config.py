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

# Gemini. The SDK reads the API key from GEMINI_API_KEY (or GOOGLE_API_KEY).
GEMINI_MODEL_LOADER = os.getenv("GEMINI_MODEL_LOADER", "gemini-3.5-flash-lite")
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "300000"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")