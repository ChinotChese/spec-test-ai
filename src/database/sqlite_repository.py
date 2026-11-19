"""SQLite repository for SpecTestAI.

Tables (see migrations/*.sql): projects, documents, source_passages,
requirements, srs_runs, plus junction tables for the many-to-many links:
  - project_documents      (PROJECTS        }o--o{ DOCUMENTS)
  - requirement_evidence   (SOURCE_PASSAGES }o--o{ REQUIREMENTS)
  - srs_run_requirements   (REQUIREMENTS    }o--o{ SRS_RUNS)

Referential integrity is enforced by SQLite foreign keys. Rules a foreign key
cannot express are checked here, inside the same transaction as the write:
  - an SRS run only includes approved requirements;
  - a requirement only cites passages from its project's documents
    ("project scope");
  - an SRS run only includes requirements of its project.
Every requirement and SRS run belongs to a project (project_id NOT NULL,
migration 003), and every write that creates one takes a project_id.

The database is built and versioned by database.migrate. Opening a brand-new
file runs every migration; an existing file that is behind is refused with
the command to migrate it (which takes a backup first).

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

from database import migrate
from database.evidence_rules import evidence_problems

REQUIREMENT_STATUSES = ("pending", "approved", "rejected")

# Requirement columns that update_requirement() may change (status has its own method).
_REQUIREMENT_CONTENT_FIELDS = frozenset({
    "type", "statement", "priority",
    "feature", "stimulus_or_condition", "response",
    "nfr_category", "verification_method", "acceptance_measure",
})

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
        """Build a new database, or check that an existing one is up to date."""
        try:
            migrate.prepare(self.conn)
        except (migrate.MigrationError, sqlite3.Error) as e:
            self.conn.close()  # release the file lock (Windows) so it can be backed up / reset
            raise RepositoryError(f"{self.db_path}: {e}") from e

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- projects

    def add_project(
        self,
        name: str,
        *,
        description: str | None = None,
        document_ids: Sequence[str] = (),
        project_id: str | None = None,
    ) -> str:
        """Create a project, optionally linked to documents, in one transaction."""
        project_id = project_id or _new_id()
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
                    (project_id, name, description),
                )
                self._link_documents(project_id, document_ids)
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add project {name!r}: {e}") from e
        return project_id

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._project_from_row(row) if row is not None else None

    def list_projects(self, *, document_id: str | None = None) -> list[dict[str, Any]]:
        """All projects, or the projects that use one document."""
        if document_id is None:
            rows = self.conn.execute("SELECT * FROM projects ORDER BY created_at, rowid")
        else:
            rows = self.conn.execute(
                "SELECT p.* FROM projects p JOIN project_documents pd ON pd.project_id = p.id "
                "WHERE pd.document_id = ? ORDER BY p.created_at, p.rowid",
                (document_id,),
            )
        return [self._project_from_row(r) for r in rows.fetchall()]

    def update_project(
        self, project_id: str, *, name: str | None = None, description: str | None = None
    ) -> None:
        """Rename a project and/or change its description (None = keep)."""
        sets, params = [], []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if not sets:
            return
        try:
            with self.conn:
                cur = self.conn.execute(
                    f"UPDATE projects SET {', '.join(sets)}, updated_at = {_NOW} WHERE id = ?",
                    [*params, project_id],
                )
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot update project {project_id!r}: {e}") from e
        if cur.rowcount == 0:
            raise RepositoryError(f"Project {project_id!r} not found")

    def delete_project(self, project_id: str) -> None:
        """Delete a project and its document links. Fails while it owns requirements or SRS runs."""
        try:
            with self.conn:
                self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        except sqlite3.IntegrityError as e:
            raise RepositoryError(
                f"Project {project_id!r} still owns requirements or SRS runs; "
                "reassign or delete them first"
            ) from e

    def link_document(self, project_id: str, document_id: str) -> None:
        """Add a document to a project (no-op if already linked)."""
        try:
            with self.conn:
                self._link_documents(project_id, [document_id])
        except sqlite3.IntegrityError as e:
            raise RepositoryError(
                f"Cannot link document {document_id!r} to project {project_id!r}: {e}"
            ) from e

    def unlink_document(self, project_id: str, document_id: str) -> None:
        """Remove a document from a project. Refused while a requirement of the
        project cites one of the document's passages."""
        with self.conn:
            citing = self.conn.execute(
                "SELECT DISTINCT r.id FROM requirements r "
                "JOIN requirement_evidence re ON re.requirement_id = r.id "
                "JOIN source_passages sp ON sp.id = re.passage_id "
                "WHERE r.project_id = ? AND sp.document_id = ? ORDER BY r.id",
                (project_id, document_id),
            ).fetchall()
            if citing:
                raise RepositoryError(
                    f"Document {document_id!r} is cited by requirements of project "
                    f"{project_id!r}: {[r[0] for r in citing]}"
                )
            self.conn.execute(
                "DELETE FROM project_documents WHERE project_id = ? AND document_id = ?",
                (project_id, document_id),
            )

    def list_project_documents(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT d.* FROM documents d JOIN project_documents pd ON pd.document_id = d.id "
            "WHERE pd.project_id = ? ORDER BY d.created_at, d.rowid",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _link_documents(self, project_id: str, document_ids: Iterable[str]) -> None:
        """INSERT OR IGNORE links. Must run inside the caller's transaction."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO project_documents (project_id, document_id) VALUES (?, ?)",
            [(project_id, d) for d in document_ids],
        )

    def _project_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["document_ids"] = [
            r[0]
            for r in self.conn.execute(
                "SELECT document_id FROM project_documents WHERE project_id = ? ORDER BY rowid",
                (d["id"],),
            )
        ]
        return d

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

    def save_document_with_passages(
        self,
        document_id: str,
        filename: str,
        file_hash: str,
        file_path: str,
        passages: Sequence[tuple[str, int | None, str]],
        project_id: str | None = None,
    ) -> str:
        """Insert a document and its (passage_id, page, text) rows in one transaction.

        If a document with this file_hash already exists, its id is reused and
        the passages are attached to it. With a project_id, the document is
        also linked to that project in the same transaction. Either everything
        is written or nothing. Returns the document id.
        """
        try:
            with self.conn:
                existing = self.conn.execute(
                    "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
                ).fetchone()
                if existing is not None:
                    document_id = existing["id"]
                else:
                    self.conn.execute(
                        "INSERT INTO documents (id, filename, file_hash, file_path) "
                        "VALUES (?, ?, ?, ?)",
                        (document_id, filename, file_hash, file_path),
                    )
                self.conn.executemany(
                    "INSERT INTO source_passages (id, document_id, page, text) VALUES (?, ?, ?, ?)",
                    [(pid, document_id, page, text) for pid, page, text in passages],
                )
                if project_id is not None:
                    self._link_documents(project_id, [document_id])
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot save document {filename!r}: {e}") from e
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
        rows = self.conn.execute("SELECT * FROM documents ORDER BY created_at, rowid").fetchall()
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
                "SELECT * FROM source_passages ORDER BY document_id, page, created_at, rowid"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM source_passages WHERE document_id = ? ORDER BY page, created_at, rowid",
                (document_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_requirements_for_passage(self, passage_id: str) -> list[dict[str, Any]]:
        """Reverse trace: which requirements cite this passage as evidence."""
        rows = self.conn.execute(
            "SELECT r.* FROM requirements r "
            "JOIN requirement_evidence re ON re.requirement_id = r.id "
            "WHERE re.passage_id = ? ORDER BY r.created_at, r.rowid",
            (passage_id,),
        ).fetchall()
        return [self._requirement_from_row(r) for r in rows]

    # ----------------------------------------------------------- requirements

    def add_requirement(
        self,
        type: str,
        statement: str,
        *,
        priority: str,
        evidence: Sequence[dict[str, Any] | str] = (),
        feature: str | None = None,
        stimulus_or_condition: str | None = None,
        response: str | None = None,
        nfr_category: str | None = None,
        verification_method: str | None = None,
        acceptance_measure: str | None = None,
        status: str = "pending",
        project_id: str,
        requirement_id: str | None = None,
    ) -> str:
        """Insert a requirement of a project.

        Keyword names match schemas.requirement.RequirementDraft, so a draft
        can be saved with `repo.add_requirement(**draft.model_dump(), project_id=...)`.
        Evidence items are passage ids or dicts with "passage_id" (+ "quote").
        project_id is required, the project must exist, and every cited
        passage must come from a document of that project.
        """
        requirement_id = requirement_id or _new_id()
        try:
            with self.conn:
                self._insert_requirement(
                    requirement_id, type, statement, priority=priority, evidence=evidence,
                    feature=feature, stimulus_or_condition=stimulus_or_condition,
                    response=response, nfr_category=nfr_category,
                    verification_method=verification_method,
                    acceptance_measure=acceptance_measure, status=status,
                    project_id=project_id,
                )
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add requirement {requirement_id!r}: {e}") from e
        return requirement_id

    def add_requirements_with_sequential_ids(
        self, rows: Sequence[dict[str, Any]], *, prefix: str, width: int = 4
    ) -> list[str]:
        """Insert several requirements in ONE transaction, assigning ids
        <prefix><n> (zero-padded to `width`) after the highest existing one.

        Each row holds add_requirement() keyword arguments (type, statement,
        priority, evidence, project_id, ...; project_id is required); a row's
        own `requirement_id` is not allowed.
        Either every row is stored or none is. Returns the new ids in order.
        """
        if any("requirement_id" in row for row in rows):
            raise RepositoryError("Rows must not carry requirement_id; ids are assigned here")
        new_ids: list[str] = []
        try:
            with self.conn:
                existing = self.conn.execute(
                    "SELECT id FROM requirements WHERE substr(id, 1, ?) = ?",
                    (len(prefix), prefix),
                ).fetchall()
                numbers = [int(r[0][len(prefix):]) for r in existing if r[0][len(prefix):].isdigit()]
                next_number = max(numbers, default=0) + 1
                for offset, row in enumerate(rows):
                    requirement_id = f"{prefix}{next_number + offset:0{width}d}"
                    row = dict(row)
                    self._insert_requirement(
                        requirement_id, row.pop("type"), row.pop("statement"), **row
                    )
                    new_ids.append(requirement_id)
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot add requirements (nothing was saved): {e}") from e
        return new_ids

    def _insert_requirement(
        self,
        requirement_id: str,
        type: str,
        statement: str,
        *,
        priority: str,
        evidence: Sequence[dict[str, Any] | str] = (),
        feature: str | None = None,
        stimulus_or_condition: str | None = None,
        response: str | None = None,
        nfr_category: str | None = None,
        verification_method: str | None = None,
        acceptance_measure: str | None = None,
        status: str = "pending",
        project_id: str | None = None,
    ) -> None:
        """INSERT one requirement + its evidence. Must run inside the caller's transaction."""
        self._check_status(status)
        self._check_project_exists(project_id)
        evidence_list = self._normalize_evidence(evidence)
        values = {
            "id": requirement_id,
            "type": type,
            "statement": statement,
            "status": status,
            "priority": priority,
            "feature": feature,
            "stimulus_or_condition": stimulus_or_condition,
            "response": response,
            "nfr_category": nfr_category,
            "verification_method": verification_method,
            "acceptance_measure": acceptance_measure,
            "project_id": project_id,
        }
        columns = ", ".join(values)
        placeholders = ", ".join("?" * len(values))
        self.conn.execute(
            f"INSERT INTO requirements ({columns}) VALUES ({placeholders})",
            list(values.values()),
        )
        self._insert_evidence(requirement_id, evidence_list)
        self._assert_in_project_scope(requirement_id)
        if status == "approved":
            self._assert_approvable(requirement_id)

    def update_requirement(
        self,
        requirement_id: str,
        *,
        evidence: Sequence[dict[str, Any] | str] | None = None,
        **fields: Any,
    ) -> None:
        """Update content fields; passing `evidence` replaces the whole evidence list.

        Allowed fields: type, statement, priority, feature, stimulus_or_condition,
        response, nfr_category, verification_method, acceptance_measure.
        Pass a field as None to clear it (e.g. stimulus_or_condition=None).
        Use set_requirement_status() to change the review status and
        set_requirement_project() to change the project.
        On an approved requirement, the change is refused (nothing stored) if it
        would leave the evidence invalid.
        """
        unknown = set(fields) - _REQUIREMENT_CONTENT_FIELDS
        if unknown:
            raise RepositoryError(f"Unknown requirement fields: {sorted(unknown)}")
        sets = [f"{name} = ?" for name in fields]
        params = list(fields.values())
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
                    self._assert_in_project_scope(requirement_id)
                status = self.conn.execute(
                    "SELECT status FROM requirements WHERE id = ?", (requirement_id,)
                ).fetchone()[0]
                if status == "approved":
                    self._assert_approvable(
                        requirement_id, "be changed this way while approved (nothing was changed)"
                    )
        except sqlite3.IntegrityError as e:
            raise RepositoryError(f"Cannot update requirement {requirement_id!r}: {e}") from e

    def set_requirement_status(self, requirement_id: str, status: str) -> None:
        """Change the review status.

        "approved" is refused (status unchanged) unless the requirement's stored
        evidence is valid (database.evidence_rules), checked in the same
        transaction as the update.
        """
        self._check_status(status)
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE requirements SET status = ?, updated_at = {_NOW} WHERE id = ?",
                (status, requirement_id),
            )
            if cur.rowcount == 0:
                raise RepositoryError(f"Requirement {requirement_id!r} not found")
            if status == "approved":
                self._assert_approvable(requirement_id)

    def set_requirement_project(
        self, requirement_id: str, project_id: str, *, link_documents: bool = False
    ) -> None:
        """Move a requirement to another project (e.g. ingested into the wrong one).

        Refused (nothing changed) when a cited passage comes from a document
        outside the project, unless link_documents=True, which first adds the
        missing documents to the project: use it only after a human confirmed
        those documents really belong to it. Also refused when the requirement
        is included in an SRS run of another project.
        """
        with self.conn:
            if self.conn.execute(
                "SELECT 1 FROM requirements WHERE id = ?", (requirement_id,)
            ).fetchone() is None:
                raise RepositoryError(f"Requirement {requirement_id!r} not found")
            self._check_project_exists(project_id)
            if link_documents:
                self._link_documents(project_id, self.requirement_document_ids(requirement_id))
            other_runs = self.conn.execute(
                "SELECT s.id FROM srs_runs s JOIN srs_run_requirements sr ON sr.srs_run_id = s.id "
                "WHERE sr.requirement_id = ? AND s.project_id != ? ORDER BY s.id",
                (requirement_id, project_id),
            ).fetchall()
            if other_runs:
                raise RepositoryError(
                    f"{requirement_id} is included in SRS runs of another project: "
                    f"{[r[0] for r in other_runs]}"
                )
            self.conn.execute(
                f"UPDATE requirements SET project_id = ?, updated_at = {_NOW} WHERE id = ?",
                (project_id, requirement_id),
            )
            self._assert_in_project_scope(requirement_id)

    def requirement_document_ids(self, requirement_id: str) -> list[str]:
        """Documents whose passages the requirement cites, in citation order."""
        rows = self.conn.execute(
            "SELECT sp.document_id FROM requirement_evidence re "
            "JOIN source_passages sp ON sp.id = re.passage_id "
            "WHERE re.requirement_id = ? ORDER BY re.rowid",
            (requirement_id,),
        ).fetchall()
        return list(dict.fromkeys(r[0] for r in rows))

    def project_scope_problems(self, requirement_id: str) -> list[str]:
        """Cited documents outside the requirement's project; [] when in scope."""
        row = self.conn.execute(
            "SELECT project_id FROM requirements WHERE id = ?", (requirement_id,)
        ).fetchone()
        if row is None:
            return []
        linked = {
            r[0]
            for r in self.conn.execute(
                "SELECT document_id FROM project_documents WHERE project_id = ?", (row[0],)
            )
        }
        return [
            f"cites document {d} which is not linked to project {row[0]}"
            for d in self.requirement_document_ids(requirement_id)
            if d not in linked
        ]

    def _assert_in_project_scope(self, requirement_id: str) -> None:
        """Raise inside the caller's transaction so the change is rolled back."""
        if problems := self.project_scope_problems(requirement_id):
            raise RepositoryError(f"{requirement_id}: {'; '.join(problems)}")

    def approval_problems(self, requirement_id: str) -> list[str]:
        """Why the requirement's stored evidence is not valid; [] means approvable."""
        evidence = [
            dict(r)
            for r in self.conn.execute(
                "SELECT passage_id, quote FROM requirement_evidence "
                "WHERE requirement_id = ? ORDER BY rowid",
                (requirement_id,),
            )
        ]
        texts = {p["id"]: p["text"] for p in self.get_passages([e["passage_id"] for e in evidence])}
        return evidence_problems(evidence, texts)

    def _assert_approvable(self, requirement_id: str, action: str = "be approved") -> None:
        """Raise inside the caller's transaction so the change is rolled back."""
        problems = self.approval_problems(requirement_id)
        if problems:
            raise RepositoryError(
                f"{requirement_id} cannot {action}, invalid evidence: {'; '.join(problems)}"
            )

    def get_requirement(self, requirement_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM requirements WHERE id = ?", (requirement_id,)
        ).fetchone()
        return self._requirement_from_row(row) if row is not None else None

    def list_requirements(
        self,
        status: str | None = None,
        *,
        type: str | None = None,
        feature: str | None = None,
        nfr_category: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List requirements; every given filter must match."""
        if status is not None:
            self._check_status(status)
        filters = {
            "status": status, "type": type, "feature": feature, "nfr_category": nfr_category,
            "project_id": project_id,
        }
        active = {k: v for k, v in filters.items() if v is not None}
        where = " AND ".join(f"{k} = ?" for k in active)
        rows = self.conn.execute(
            f"SELECT * FROM requirements {'WHERE ' + where if where else ''} "
            "ORDER BY created_at, rowid",
            list(active.values()),
        ).fetchall()
        return [self._requirement_from_row(r) for r in rows]

    def get_approved_requirements(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self.list_requirements(status="approved", project_id=project_id)

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
        *,
        project_id: str,
    ) -> str:
        """Record an SRS run of a project. By default every included requirement
        must be approved; every one must belong to that project."""
        ids = list(dict.fromkeys(requirement_ids))  # dedupe, keep order
        self._check_project_exists(project_id)
        self._check_requirements_exist(ids, require_approved=require_approved)
        self._check_requirements_in_project(ids, project_id)
        run_id = run_id or _new_id()
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO srs_runs (id, srs_json, output_path, project_id) "
                    "VALUES (?, ?, ?, ?)",
                    (run_id, json.dumps(srs, ensure_ascii=False), output_path, project_id),
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

    def list_srs_runs(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        """All SRS runs, or the runs of one project."""
        if project_id is not None:
            where, params = "WHERE project_id = ?", [project_id]
        else:
            where, params = "", []
        rows = self.conn.execute(
            f"SELECT * FROM srs_runs {where} ORDER BY created_at, rowid", params
        ).fetchall()
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

    def _check_project_exists(self, project_id: str | None) -> None:
        if project_id is None:
            raise RepositoryError("project_id is required: every requirement and SRS run belongs to a project")
        if self.conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise RepositoryError(f"Project {project_id!r} not found")

    def _check_requirements_in_project(self, ids: Sequence[str], project_id: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT id FROM requirements WHERE id IN ({placeholders}) "
            "AND project_id != ? ORDER BY id",
            [*ids, project_id],
        ).fetchall()
        if rows:
            raise RepositoryError(
                f"SRS run for project {project_id!r} includes requirements of another "
                f"project: {[r[0] for r in rows]}"
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
