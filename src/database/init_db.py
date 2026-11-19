"""Create or check the SpecTestAI database.

    uv run python -m database.init_db            # create if missing, then check it
    uv run python -m database.init_db --reset    # DELETE the database file and recreate it
    uv run python -m database.init_db --db PATH  # use another file (default: config.DB_PATH)

Run from src/. A new database is built by running every migration
(database/migrations/NNN_name.sql). An existing one is only checked: if it is
behind, this prints the pending migrations and the command that applies them
with a backup (python -m database.migrate up). Data is never deleted except
with --reset.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import config
from database import migrate
from database.sqlite_repository import SQLiteRepository


def expected_layout() -> dict[str, list[str]]:
    """Table -> column names of a fresh database migrated to the latest version."""
    return migrate.layout_at()


def schema_drift(conn: sqlite3.Connection) -> list[str]:
    """Differences between the database and the latest migration; empty when they match."""
    return migrate.layout_drift(migrate.layout(conn), expected_layout())


def init_db(db_path: str | Path = config.DB_PATH, *, reset: bool = False) -> migrate.Status:
    """Create the database (and folder) if needed. Returns its migration status."""
    db_path = Path(db_path)
    if reset:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    if not db_path.exists():
        SQLiteRepository(db_path).close()  # runs every migration
    conn = sqlite3.connect(db_path)
    try:
        return migrate.status(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    parser.add_argument("--reset", action="store_true", help="delete the file and recreate it")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    existed = db_path.exists()
    st = init_db(db_path, reset=args.reset)
    action = "recreated" if args.reset and existed else ("opened" if existed and not args.reset else "created")
    print(f"Database {action}: {db_path.resolve()}")
    print(f"Migrations: {migrate.MIGRATIONS_DIR}  (version {st.current} of {st.latest})")

    conn = sqlite3.connect(db_path)
    try:
        for table in migrate.layout(conn):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table:<24} {count:>6} rows")
        drift = schema_drift(conn) if st.up_to_date else []
    finally:
        conn.close()

    if st.problems:
        print("\nCannot be migrated:")
        for p in st.problems:
            print(f"  - {p}")
        return 1
    if not st.up_to_date:
        print("\nDatabase is older than the code. Pending migrations:")
        for m in st.pending:
            print(f"  - {m.label}")
        print("Fix (keeps your data, backs the file up first): python -m database.migrate up")
        return 1
    if drift:
        print("\nSchema drift (tables changed outside the migrations):")
        for line in drift:
            print(f"  - {line}")
        return 1
    print("Schema OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
