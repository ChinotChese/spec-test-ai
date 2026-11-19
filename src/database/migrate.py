"""Versioned schema migrations for the SpecTestAI SQLite database.

    uv run python -m database.migrate              # status: applied / pending migrations
    uv run python -m database.migrate up           # back up the file, apply pending migrations
    uv run python -m database.migrate up --to 2    # stop at version 2
    uv run python -m database.migrate up --no-backup
    uv run python -m database.migrate --db PATH ...

Run from src/. Migrations are the files migrations/NNN_name.sql (NNN = 001,
002, ... with no gaps), applied in order. Each one runs in ONE transaction
together with its row in schema_migrations: it is applied completely or not
at all, never half (ALTER TABLE ADD COLUMN cannot be re-run, so a half-applied
file would leave the database stuck).

Rules for migration files:
  - never edit a file once it has been applied anywhere; add the next one
    (the checksum of every applied file is stored and checked);
  - no BEGIN / COMMIT / PRAGMA inside the file: the runner owns the
    transaction, runs it with foreign keys OFF (needed for table rebuilds) and
    finishes with PRAGMA foreign_key_check before committing;
  - schema only: a migration must not guess data. If it needs data that is
    missing (e.g. a NOT NULL column), it fails, is rolled back, and the data
    is fixed by hand before running it again.

Databases created before this framework (by the old schema.sql) have no
schema_migrations table. If their layout is exactly migration 001's, `up`
records 001 as a baseline instead of running it; any other layout is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")

_FILE_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
# Statement-level transaction control and pragmas. A trigger body's BEGIN ... END is fine.
_FORBIDDEN_RE = re.compile(
    r"^\s*(COMMIT|ROLLBACK|PRAGMA|END\s+TRANSACTION"
    r"|BEGIN(?=\s*;|\s+(?:TRANSACTION|DEFERRED|IMMEDIATE|EXCLUSIVE)\b))\b",
    re.IGNORECASE | re.MULTILINE,
)
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

TRACKING_TABLE = "schema_migrations"
_TRACKING_DDL = f"""
CREATE TABLE IF NOT EXISTS {TRACKING_TABLE} (
    version     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    checksum    TEXT    NOT NULL,                     -- sha256 of the file (LF line endings)
    baseline    INTEGER NOT NULL DEFAULT 0 CHECK (baseline IN (0, 1)),  -- 1 = stamped, not run
    applied_at  TEXT    NOT NULL DEFAULT ({_NOW})
)
"""


class MigrationError(Exception):
    """The database cannot be migrated as asked; nothing was changed by the failing step."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        # Normalise line endings: git autocrlf must not make an applied file look edited.
        text = self.sql.replace("\r\n", "\n")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        return f"{self.version:03d}_{self.name}"


@dataclass
class Status:
    current: int                      # highest applied version, 0 for none
    latest: int                       # highest version on disk
    applied: list[int] = field(default_factory=list)
    pending: list[Migration] = field(default_factory=list)
    needs_baseline: bool = False      # pre-framework database with the 001 layout
    fresh: bool = False               # no tables at all
    problems: list[str] = field(default_factory=list)  # blocking: fix before migrating

    @property
    def up_to_date(self) -> bool:
        return not self.pending and not self.problems and not self.needs_baseline


# ---------------------------------------------------------------- discovery


def discover(directory: Path | None = None) -> list[Migration]:
    """Migration files in version order. Versions must be 1..N without gaps."""
    directory = MIGRATIONS_DIR if directory is None else directory
    migrations = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        m = _FILE_RE.match(path.name)
        if not m:
            raise MigrationError(f"Bad migration file name {path.name!r}; expected NNN_name.sql")
        migrations.append(Migration(int(m.group(1)), m.group(2), path))
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Migration versions must be 1..N without gaps or duplicates: {versions}")
    for m in migrations:
        if bad := _FORBIDDEN_RE.search(_strip_comments(m.sql)):
            raise MigrationError(
                f"{m.path.name}: {bad.group(1).upper()} is not allowed in a migration file "
                "(the runner owns the transaction and the foreign key pragma)"
            )
    return migrations


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


# ------------------------------------------------------------------- layout


def layout(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Table -> column names, without SQLite's and the migration tracking tables."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != ? ORDER BY name",
            (TRACKING_TABLE,),
        )
    ]
    return {t: [c[1] for c in conn.execute(f"PRAGMA table_info({t})")] for t in tables}


@cache
def _layout_at_cached(version: int, checksums: tuple[str, ...]) -> tuple:
    conn = sqlite3.connect(":memory:")
    try:
        migrate(conn, target=version)
        return tuple((t, tuple(c)) for t, c in layout(conn).items())
    finally:
        conn.close()


def layout_at(version: int | None = None) -> dict[str, list[str]]:
    """Layout of a fresh database migrated to `version` (default: latest)."""
    migrations = discover()
    version = migrations[-1].version if version is None else version
    key = tuple(m.checksum for m in migrations[:version])
    return {t: list(c) for t, c in _layout_at_cached(version, key)}


def layout_drift(actual: dict[str, list[str]], expected: dict[str, list[str]]) -> list[str]:
    """Differences between two layouts; empty when they match."""
    problems = []
    for table, columns in expected.items():
        if table not in actual:
            problems.append(f"missing table {table}")
            continue
        if missing := [c for c in columns if c not in actual[table]]:
            problems.append(f"{table}: missing columns {missing}")
        if extra := [c for c in actual[table] if c not in columns]:
            problems.append(f"{table}: unexpected columns {extra}")
    problems += [f"unexpected table {t}" for t in actual if t not in expected]
    return problems


# ------------------------------------------------------------------- status


def _has_tracking_table(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TRACKING_TABLE,)
    ).fetchone() is not None


def _applied_rows(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    if not _has_tracking_table(conn):
        return {}
    return {
        v: (name, checksum)
        for v, name, checksum in conn.execute(
            f"SELECT version, name, checksum FROM {TRACKING_TABLE} ORDER BY version"
        )
    }


def status(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> Status:
    migrations = discover() if migrations is None else migrations
    by_version = {m.version: m for m in migrations}
    applied = _applied_rows(conn)
    st = Status(
        current=max(applied, default=0),
        latest=migrations[-1].version if migrations else 0,
        applied=sorted(applied),
    )

    if not applied:
        tables = layout(conn)
        if not tables:
            st.fresh = True
        else:
            drift = layout_drift(tables, layout_at(1))
            if drift:
                st.problems.append(
                    "database has no migration history and its tables do not match "
                    f"migration 001 ({'; '.join(drift)}). It was created by an older "
                    "schema that cannot be migrated; export what you need, then "
                    "recreate it with: python -m database.init_db --reset"
                )
            else:
                st.needs_baseline = True

    for version, (name, checksum) in applied.items():
        m = by_version.get(version)
        if m is None:
            st.problems.append(
                f"applied migration {version:03d}_{name} is not on disk "
                "(the database is newer than this code)"
            )
        elif m.checksum != checksum:
            st.problems.append(
                f"{m.path.name} was edited after it was applied (checksum mismatch); "
                "restore the file and put the change in a new migration"
            )

    done = set(applied) | ({1} if st.needs_baseline else set())
    st.pending = [m for m in migrations if m.version not in done]
    if st.pending and applied and st.pending[0].version < st.current:
        st.problems.append(
            f"migration {st.pending[0].label} is missing from the history although "
            f"{st.current:03d} is applied"
        )
    return st


# -------------------------------------------------------------------- apply


def migrate(
    conn: sqlite3.Connection, *, target: int | None = None
) -> list[Migration]:
    """Bring the database to `target` (default: latest). Returns what was run or stamped.

    Each migration is its own transaction; on error that migration is rolled
    back, earlier ones stay applied, and MigrationError is raised.
    """
    migrations = discover()
    if target is not None and not 0 <= target <= len(migrations):
        raise MigrationError(f"Unknown target version {target}")
    st = status(conn, migrations)
    if st.problems:
        raise MigrationError("; ".join(st.problems))
    if target is not None and target < st.current:
        raise MigrationError(
            f"Database is at version {st.current}; downgrading to {target} is not supported "
            "(restore a backup instead)"
        )

    if conn.in_transaction:
        raise MigrationError("Cannot migrate inside an open transaction")
    conn.execute(_TRACKING_DDL)
    conn.commit()

    done: list[Migration] = []
    if st.needs_baseline:
        _record(conn, migrations[0], baseline=True)
        conn.commit()
        done.append(migrations[0])
    for m in st.pending:
        if target is not None and m.version > target:
            break
        _apply(conn, m)
        done.append(m)
    return done


def _record(conn: sqlite3.Connection, m: Migration, *, baseline: bool) -> None:
    conn.execute(
        f"INSERT INTO {TRACKING_TABLE} (version, name, checksum, baseline) VALUES (?, ?, ?, ?)",
        (m.version, m.name, m.checksum, int(baseline)),
    )


def _apply(conn: sqlite3.Connection, m: Migration) -> None:
    """Run one migration file and record it, all in one transaction."""
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    isolation = conn.isolation_level
    conn.isolation_level = None          # we issue BEGIN / COMMIT ourselves
    conn.execute("PRAGMA foreign_keys = OFF")  # no effect inside a transaction, so set it first
    try:
        # executescript() would COMMIT a pending transaction first and then run
        # in autocommit mode, so the BEGIN must be part of the script itself.
        try:
            conn.executescript("BEGIN IMMEDIATE;\n" + m.sql)
            if not conn.in_transaction:
                raise MigrationError(f"{m.path.name} ended the transaction itself")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationError(
                    f"{m.path.name} leaves {len(violations)} foreign key violation(s), "
                    f"first: {tuple(violations[0])}"
                )
            _record(conn, m, baseline=False)
            conn.execute("COMMIT")
        except BaseException as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if isinstance(e, sqlite3.Error):
                raise MigrationError(f"{m.path.name} failed, nothing from it was applied: {e}") from e
            raise
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_was_on else 'OFF'}")
        conn.isolation_level = isolation


def prepare(conn: sqlite3.Connection) -> None:
    """Used when a repository opens a database.

    A brand-new (empty) database is built by running every migration. An
    existing database must already be up to date: migrating it changes the
    file, so that is left to the explicit `up` command, which takes a backup
    first. Raises MigrationError with the command to run otherwise.
    """
    st = status(conn)
    if st.fresh:
        migrate(conn)
        return
    if st.problems:
        raise MigrationError("; ".join(st.problems))
    if not st.up_to_date:
        pending = ", ".join(m.label for m in st.pending)
        raise MigrationError(
            f"database is at version {st.current} but the code needs {st.latest} "
            f"(pending: {pending}). Back it up and migrate with: "
            "python -m database.migrate up"
        )


def backup(db_path: str | Path) -> Path:
    """Consistent copy of the database next to it: <name>.bak-<UTC timestamp>."""
    db_path = Path(db_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


# ---------------------------------------------------------------------- cli


def _print_status(st: Status, db_path: Path) -> None:
    print(f"Database: {db_path.resolve()}")
    if st.fresh:
        print("  empty database; `up` builds it from all migrations")
    print(f"  version {st.current} of {st.latest}" + ("  (pre-framework, baseline 001)" if st.needs_baseline else ""))
    for m in st.pending:
        print(f"  pending  {m.label}")
    for p in st.problems:
        print(f"  PROBLEM  {p}")


def main(argv: list[str] | None = None) -> int:
    import config

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", choices=("status", "up"), default="status")
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    parser.add_argument("--to", type=int, default=None, help="target version (default: latest)")
    parser.add_argument("--no-backup", action="store_true", help="skip the backup before `up`")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"{db_path} does not exist; create it with: python -m database.init_db")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        st = status(conn)
        _print_status(st, db_path)
        if args.command == "status":
            return 0 if st.up_to_date else 1
        if st.problems:
            return 1
        if st.up_to_date:
            print("Nothing to do.")
            return 0
        if not args.no_backup:
            print(f"Backup:   {backup(db_path)}")
        try:
            done = migrate(conn, target=args.to)
        except MigrationError as e:
            print(f"FAILED: {e}")
            return 1
        for m in done:
            print(f"  {'stamped' if m.version == 1 and st.needs_baseline else 'applied'}  {m.label}")
        after = status(conn)
        print(f"Now at version {after.current} of {after.latest}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
