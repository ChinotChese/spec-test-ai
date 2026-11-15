"""Create or check the SpecTestAI database.

    uv run python -m database.init_db            # create if missing, then check schema
    uv run python -m database.init_db --reset    # DELETE the database file and recreate it
    uv run python -m database.init_db --db PATH  # use another file (default: config.DB_PATH)

Run from src/. Opening SQLiteRepository already creates missing tables, but it
cannot change tables that exist with an older layout (there are no migrations).
This command reports such drift so it is caught before a write fails.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import config
from database.sqlite_repository import SCHEMA_PATH, SQLiteRepository


def expected_layout() -> dict[str, list[str]]:
    """Table -> column names, from a fresh in-memory database built from schema.sql."""
    with SQLiteRepository(":memory:") as repo:
        return _layout(repo.conn)


def _layout(conn: sqlite3.Connection) -> dict[str, list[str]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    return {t: [c[1] for c in conn.execute(f"PRAGMA table_info({t})")] for t in tables}


def schema_drift(conn: sqlite3.Connection) -> list[str]:
    """Differences between the database and schema.sql; empty when they match."""
    actual, expected = _layout(conn), expected_layout()
    problems = []
    for table, columns in expected.items():
        if table not in actual:
            problems.append(f"missing table {table}")
            continue
        missing = [c for c in columns if c not in actual[table]]
        extra = [c for c in actual[table] if c not in columns]
        if missing:
            problems.append(f"{table}: missing columns {missing}")
        if extra:
            problems.append(f"{table}: columns not in schema.sql {extra}")
    problems += [f"table not in schema.sql: {t}" for t in actual if t not in expected]
    return problems


def init_db(db_path: str | Path = config.DB_PATH, *, reset: bool = False) -> list[str]:
    """Create the database (and folder) if needed. Returns schema drift, if any."""
    db_path = Path(db_path)
    if reset:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    elif db_path.exists():
        # Check before running schema.sql: on an old layout it can fail midway
        # (e.g. an index on a column the old table lacks).
        conn = sqlite3.connect(db_path)
        try:
            drift = schema_drift(conn)
        finally:
            conn.close()
        missing_tables_only = all(d.startswith("missing table") for d in drift)
        if drift and not missing_tables_only:
            return drift
    with SQLiteRepository(db_path) as repo:  # creates missing tables and indexes
        return schema_drift(repo.conn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    parser.add_argument("--reset", action="store_true", help="delete the file and recreate it")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    existed = db_path.exists()
    drift = init_db(db_path, reset=args.reset)
    action = "recreated" if args.reset and existed else ("opened" if existed and not args.reset else "created")
    print(f"Database {action}: {db_path.resolve()}")
    print(f"Schema: {SCHEMA_PATH}")

    conn = sqlite3.connect(db_path)
    try:
        for table in _layout(conn):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table:<24} {count:>6} rows")
    finally:
        conn.close()

    if drift:
        print("\nSchema drift (database is older than schema.sql):")
        for line in drift:
            print(f"  - {line}")
        print("Fix: python -m database.init_db --reset   (deletes all data)")
        return 1
    print("Schema OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
