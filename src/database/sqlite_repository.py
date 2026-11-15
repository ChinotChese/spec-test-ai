"""SQLite repository for SpecTestAI.

Tables (see schema.sql): documents, source_passages, requirements, srs_runs,
plus two junction tables for the ERD's many-to-many links:
  - requirement_evidence   (SOURCE_PASSAGES }o--o{ REQUIREMENTS)
  - srs_run_requirements   (REQUIREMENTS    }o--o{ SRS_RUNS)

Referential integrity is enforced by SQLite foreign keys. The one rule a
foreign key cannot express, "an SRS run only includes approved requirements",
is checked here.

Returned dicts keep the list shape callers expect:
  requirement["evidence"]       -> [{"passage_id": ..., "quote": ...}, ...]
  srs_run["requirement_ids"]    -> ["REQ-001", ...]   (in SRS order)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

REQUIREMENT_STATUSES = ("pending", "approved", "rejected")

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class RepositoryError(Exception):
    """Raised when a write would break referential integrity or a rule."""


def _new_id() -> str:
    return uuid.uuid4().hex


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class SQLiteRepository:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    # ------------------------------------------------------------------ setup

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------------- documents

    def add_document(
        self,
        filename: str,
        file_hash: str,
        file_path: str,
        document_id: str | None = None,
    ) -> str:
        """Insert a document; if the same file_hash exists, return its id instead."""
        existing = self.get_document_by_hash(file_hash)
        if existing is not None:
            return existing["id"]
        document_id = document_id or _new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO documents (id, filename, file_hash, file_path) VALUES (?, ?, ?, ?)",
                (document_id, filename, file_hash, file_path),
            )
        return document_id

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return _row_to_dict(row)

    def get_document_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return _row_to_dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM documents ORDER BY created_at, id").fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, document_id: str) -> None:
        """Delete a document and its passages. Fails if any passage is cited as evidence."""
        try:
            with self.conn:
                self.conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        except sqlite3.IntegrityError as e:
            cited = self.conn.execute(
                "SELECT DISTINCT re.requirement_id FROM requirement_evidence re "
                "JOIN source_passages p ON p.id = re.passage_id WHERE p.document_id = ?",
                (document_id,),
            ).fetchall()
            raise RepositoryError(
                f"Document {document_id!r} has passages cited by requirements: "
                f"{[r[0] for r in cited]}"
            ) from e

    # -------------------------------------------------------- source passages

    def add_passage(
        self,
        document_id: str,
        text: str,
        page: int | None = None,
        passage_id: str | None = None,
    ) -> str:
        passage_id = passage_id or _new_id()
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO source_passages (id, document_id, page, text) VALUES (?, ?, ?, ?)",
                    (passage_id, document_id, page, text),
                )
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add passage to document {document_id!r}: {e}") from e
        return passage_id

    def add_passages(
        self, document_id: str, passages: Iterable[tuple[int | None, str]]
    ) -> list[str]:
        """Bulk insert (page, text) pairs in one transaction. Returns the new ids."""
        rows = [(_new_id(), document_id, page, text) for page, text in passages]
        try:
            with self.conn:
                self.conn.executemany(
                    "INSERT INTO source_passages (id, document_id, page, text) VALUES (?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add passages to document {document_id!r}: {e}") from e
        return [r[0] for r in rows]

    def get_passage(self, passage_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM source_passages WHERE id = ?", (passage_id,)
        ).fetchone()
        return _row_to_dict(row)

    def get_passages(self, passage_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not passage_ids:
            return []
        placeholders = ",".join("?" * len(passage_ids))
        rows = self.conn.execute(
            f"SELECT * FROM source_passages WHERE id IN ({placeholders})", list(passage_ids)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_passages(self, document_id: str | None = None) -> list[dict[str, Any]]:
        if document_id is None:
            rows = self.conn.execute(
                "SELECT * FROM source_passages ORDER BY document_id, page, created_at"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM source_passages WHERE document_id = ? ORDER BY page, created_at",
                (document_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_requirements_for_passage(self, passage_id: str) -> list[dict[str, Any]]:
        """Reverse trace: which requirements cite this passage as evidence."""
        rows = self.conn.execute(
            "SELECT r.* FROM requirements r "
            "JOIN requirement_evidence re ON re.requirement_id = r.id "
            "WHERE re.passage_id = ? ORDER BY r.created_at, r.id",
            (passage_id,),
        ).fetchall()
        return [self._requirement_from_row(r) for r in rows]

    # ----------------------------------------------------------- requirements

    def add_requirement(
        self,
        type: str,
        statement: str,
        evidence: Sequence[dict[str, Any] | str] = (),
        status: str = "pending",
        requirement_id: str | None = None,
    ) -> str:
        """Insert a requirement. Evidence items are passage ids or dicts with "passage_id"."""
        self._check_status(status)
        evidence_list = self._normalize_evidence(evidence)
        requirement_id = requirement_id or _new_id()
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO requirements (id, type, statement, status) VALUES (?, ?, ?, ?)",
                    (requirement_id, type, statement, status),
                )
                self._insert_evidence(requirement_id, evidence_list)
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add requirement {requirement_id!r}: {e}") from e
        return requirement_id

    def update_requirement(
        self,
        requirement_id: str,
        *,
        type: str | None = None,
        statement: str | None = None,
        evidence: Sequence[dict[str, Any] | str] | None = None,
    ) -> None:
        """Update fields; passing `evidence` replaces the whole evidence list."""
        sets: list[str] = []
        params: list[Any] = []
        if type is not None:
            sets.append("type = ?")
            params.append(type)
        if statement is not None:
            sets.append("statement = ?")
            params.append(statement)
        evidence_list = self._normalize_evidence(evidence) if evidence is not None else None
        if not sets and evidence_list is None:
            return
        sets.append(f"updated_at = {_NOW}")
        try:
            with self.conn:
                cur = self.conn.execute(
                    f"UPDATE requirements SET {', '.join(sets)} WHERE id = ?",
                    [*params, requirement_id],
                )
                if cur.rowcount == 0:
                    raise RepositoryError(f"Requirement {requirement_id!r} not found")
                if evidence_list is not None:
                    self.conn.execute(
                        "DELETE FROM requirement_evidence WHERE requirement_id = ?",
                        (requirement_id,),
                    )
                    self._insert_evidence(requirement_id, evidence_list)
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot update requirement {requirement_id!r}: {e}") from e

    def set_requirement_status(self, requirement_id: str, status: str) -> None:
        self._check_status(status)
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE requirements SET status = ?, updated_at = {_NOW} WHERE id = ?",
                (status, requirement_id),
            )
        if cur.rowcount == 0:
            raise RepositoryError(f"Requirement {requirement_id!r} not found")

    def get_requirement(self, requirement_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM requirements WHERE id = ?", (requirement_id,)
        ).fetchone()
        return self._requirement_from_row(row) if row is not None else None

    def list_requirements(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM requirements ORDER BY created_at, id"
            ).fetchall()
        else:
            self._check_status(status)
            rows = self.conn.execute(
                "SELECT * FROM requirements WHERE status = ? ORDER BY created_at, id", (status,)
            ).fetchall()
        return [self._requirement_from_row(r) for r in rows]

    def get_approved_requirements(self) -> list[dict[str, Any]]:
        return self.list_requirements(status="approved")

    def delete_requirement(self, requirement_id: str) -> None:
        """Delete a requirement and its evidence links. Fails if an SRS run includes it."""
        try:
            with self.conn:
                self.conn.execute("DELETE FROM requirements WHERE id = ?", (requirement_id,))
        except sqlite3.IntegrityError as e:
            raise RepositoryError(
                f"Requirement {requirement_id!r} is included in an SRS run and cannot be deleted"
            ) from e

    # --------------------------------------------------------------- srs runs

    def add_srs_run(
        self,
        srs: dict[str, Any] | list[Any],
        requirement_ids: Sequence[str],
        output_path: str | None = None,
        run_id: str | None = None,
        require_approved: bool = True,
    ) -> str:
        """Record an SRS run. By default every included requirement must be approved."""
        ids = list(dict.fromkeys(requirement_ids))  # dedupe, keep order
        self._check_requirements_exist(ids, require_approved=require_approved)
        run_id = run_id or _new_id()
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO srs_runs (id, srs_json, output_path) VALUES (?, ?, ?)",
                    (run_id, json.dumps(srs, ensure_ascii=False), output_path),
                )
                self.conn.executemany(
                    "INSERT INTO srs_run_requirements (srs_run_id, requirement_id, position) "
                    "VALUES (?, ?, ?)",
                    [(run_id, rid, pos) for pos, rid in enumerate(ids)],
                )
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add SRS run {run_id!r}: {e}") from e
        return run_id

    def set_srs_output_path(self, run_id: str, output_path: str) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE srs_runs SET output_path = ? WHERE id = ?", (output_path, run_id)
            )
        if cur.rowcount == 0:
            raise RepositoryError(f"SRS run {run_id!r} not found")

    def get_srs_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM srs_runs WHERE id = ?", (run_id,)).fetchone()
        return self._srs_run_from_row(row) if row is not None else None

    def list_srs_runs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM srs_runs ORDER BY created_at, id").fetchall()
        return [self._srs_run_from_row(r) for r in rows]

    def delete_srs_run(self, run_id: str) -> None:
        """Delete an SRS run and its requirement links (requirements are kept)."""
        with self.conn:
            self.conn.execute("DELETE FROM srs_runs WHERE id = ?", (run_id,))

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _check_status(status: str) -> None:
        if status not in REQUIREMENT_STATUSES:
            raise RepositoryError(
                f"Invalid status {status!r}; expected one of {REQUIREMENT_STATUSES}"
            )

    @staticmethod
    def _normalize_evidence(evidence: Sequence[dict[str, Any] | str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in evidence:
            if isinstance(item, str):
                item = {"passage_id": item}
            elif not isinstance(item, dict) or not item.get("passage_id"):
                raise RepositoryError(f"Evidence item must have a 'passage_id': {item!r}")
            pid = item["passage_id"]
            if pid in seen:
                raise RepositoryError(f"Passage {pid!r} is listed twice as evidence")
            seen.add(pid)
            items.append({"passage_id": pid, "quote": item.get("quote")})
        return items

    def _insert_evidence(self, requirement_id: str, evidence: list[dict[str, Any]]) -> None:
        """Insert evidence links. Must run inside the caller's transaction."""
        missing = {e["passage_id"] for e in evidence} - {
            p["id"] for p in self.get_passages([e["passage_id"] for e in evidence])
        }
        if missing:
            raise RepositoryError(f"Evidence references unknown passages: {sorted(missing)}")
        self.conn.executemany(
            "INSERT INTO requirement_evidence (requirement_id, passage_id, quote) VALUES (?, ?, ?)",
            [(requirement_id, e["passage_id"], e["quote"]) for e in evidence],
        )

    def _check_requirements_exist(self, ids: list[str], *, require_approved: bool) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT id, status FROM requirements WHERE id IN ({placeholders})", ids
        ).fetchall()
        status_by_id = {r["id"]: r["status"] for r in rows}
        missing = [i for i in ids if i not in status_by_id]
        if missing:
            raise RepositoryError(f"SRS run references unknown requirements: {missing}")
        if require_approved:
            not_approved = [i for i in ids if status_by_id[i] != "approved"]
            if not_approved:
                raise RepositoryError(
                    f"SRS run includes requirements that are not approved: {not_approved}"
                )

    def _requirement_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["evidence"] = [
            dict(e)
            for e in self.conn.execute(
                "SELECT passage_id, quote FROM requirement_evidence "
                "WHERE requirement_id = ? ORDER BY rowid",
                (d["id"],),
            ).fetchall()
        ]
        return d

    def _srs_run_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["srs"] = json.loads(d["srs_json"])
        d["requirement_ids"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT requirement_id FROM srs_run_requirements "
                "WHERE srs_run_id = ? ORDER BY position",
                (d["id"],),
            ).fetchall()
        ]
        return d
