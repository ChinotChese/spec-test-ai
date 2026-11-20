"""Logging for the command-line tools: console as before, plus a daily log file.

    from app_logging import setup_cli_logging
    setup_cli_logging("srs_generator", quiet_http=True)

Console: "LEVEL message" at INFO, unchanged from the old basicConfig().
File (when SPECTEST_LOG_DIR is set, default src/logs/):
    <dir>/spectest-YYYY-MM-DD.log, appended, UTF-8, one file per day shared by
    every tool; each line carries time, level, tool name, pid and logger:
    2026-11-20 07:23:36 INFO [srs_generator 1234] retrieval.retriever: retrieval hybrid: ...
    Level: SPECTEST_LOG_LEVEL (default INFO; DEBUG adds e.g. per-query
    retrieval hits). HTTP client request lines (httpx / httpcore) below
    WARNING are not written to the file: they repeat what the tools log.

SPECTEST_LOG_DIR=off (or empty) disables the file. A log directory that
cannot be created only prints a warning: logging never stops a run.
Log files are gitignored (*.log, src/logs/) and contain client data: file
names, document / passage / requirement ids, model error messages, and up to
100 characters of rejected passages / requirement statements.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from pathlib import Path

import config

CONSOLE_FORMAT = "%(levelname)s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)s [{tool} %(process)d] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_HTTP_LOGGERS = ("httpx", "httpcore")
_MARK = "_spectest_handler"


class _DropHttpChatter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        noisy = record.name.split(".")[0].startswith(_HTTP_LOGGERS)
        return not noisy or record.levelno >= logging.WARNING


def log_file_path(log_dir: Path, day: dt.date | None = None) -> Path:
    return log_dir / f"spectest-{(day or dt.date.today()).isoformat()}.log"


def setup_cli_logging(
    tool: str,
    *,
    quiet_http: bool = False,
    log_dir: Path | None | str = "default",
    file_level: str | int | None = None,
) -> Path | None:
    """Configure the root logger for a CLI run; returns the log file (or None).

    `quiet_http`: also hide httpx INFO request lines on the console.
    `log_dir` / `file_level` override SPECTEST_LOG_DIR / SPECTEST_LOG_LEVEL (tests).
    Safe to call more than once: handlers added earlier by it are replaced.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(h)
        h.close()

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    setattr(console, _MARK, True)
    root.addHandler(console)
    if quiet_http:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    directory = config.LOG_DIR if log_dir == "default" else (Path(log_dir) if log_dir else None)
    level = _level(file_level if file_level is not None else config.LOG_LEVEL)
    root.setLevel(min(logging.INFO, level) if directory else logging.INFO)
    if directory is None:
        return None

    path = log_file_path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    except OSError as e:
        root.setLevel(logging.INFO)
        logging.getLogger(__name__).warning("log file disabled: cannot write %s (%s)", path, e)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FILE_FORMAT.format(tool=tool), DATE_FORMAT))
    handler.addFilter(_DropHttpChatter())
    setattr(handler, _MARK, True)
    root.addHandler(handler)
    if level < logging.INFO:  # e.g. DEBUG for the file only: keep httpx quiet there anyway
        for name in _HTTP_LOGGERS:
            if logging.getLogger(name).level == logging.NOTSET:
                logging.getLogger(name).setLevel(logging.INFO)
    logging.getLogger(__name__).debug("log file %s", path)
    handler.handle(logging.LogRecord(__name__, logging.INFO, __file__, 0,
                                     "start: %s (cwd %s)", (" ".join(sys.argv), os.getcwd()), None))
    return path


def _level(value: str | int) -> int:
    if isinstance(value, int):
        return value
    level = logging.getLevelName(str(value).strip().upper())
    return level if isinstance(level, int) else logging.INFO
