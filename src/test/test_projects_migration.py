"""Migration framework, 002_projects, project backfill and project-scope rules.

Run from src/:  python -m unittest discover -s test -t .

Fixtures are synthetic. A "legacy" database is built from migration 001 alone,
without schema_migrations, exactly like a file created by the old schema.sql.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from database import migrate, project_backfill
from database.sqlite_repository import RepositoryError, SQLiteRepository
from schemas.requirement import Requirement

FR = {"priority": "must", "feature": "Login"}


def build_legacy_db(path: Path) -> None:
    """Pre-framework database (001 layout, no history) with data covering every backfill case.

    documents A, B, C, D;  requirements:
      RQ-1  evidence A          -> unambiguous
      RQ-2  evidence A + B      -> multi-document: review
      RQ-3  no evidence         -> review
      RQ-4  evidence C          -> C is mapped to two projects: review
      RQ-5  evidence D          -> D is in no project: review
    SRS runs: RUN-1 [RQ-1] -> unambiguous;  RUN-2 [RQ-1, RQ-2] -> review
    """
    conn = sqlite3.connect(path)
    conn.executescript(migrate.discover()[0].sql)
    for doc in "ABCD":
        conn.execute(
            "INSERT INTO documents (id, filename, file_hash, file_path) VALUES (?, ?, ?, ?)",
            (f"DOC-{doc}", f"doc_{doc}.md", f"hash-{doc}", f"input/doc_{doc}.md"),
        )
        conn.execute(
            "INSERT INTO source_passages (id, document_id, text) VALUES (?, ?, ?)",
            (f"P-{doc}", f"DOC-{doc}", f"The system shall do thing {doc}."),
        )
    for rid, status, docs in [
        ("RQ-1", "approved", "A"), ("RQ-2", "approved", "AB"), ("RQ-3", "pending", ""),
        ("RQ-4", "pending", "C"), ("RQ-5", "rejected", "D"),
    ]:
        conn.execute(
            "INSERT INTO requirements (id, type, statement, status, priority, feature) "
            "VALUES (?, 'functional', ?, ?, 'must', 'Login')",
            (rid, f"The system shall {rid}.", status),
        )
        for doc in docs:
            conn.execute(
                "INSERT INTO requirement_evidence (requirement_id, passage_id, quote) VALUES (?, ?, ?)",
                (rid, f"P-{doc}", f"do thing {doc}"),
            )
    for run, reqs in [("RUN-1", ["RQ-1"]), ("RUN-2", ["RQ-1", "RQ-2"])]:
        conn.execute("INSERT INTO srs_runs (id, srs_json) VALUES (?, '{}')", (run,))
        for pos, rid in enumerate(reqs):
            conn.execute(
                "INSERT INTO srs_run_requirements (srs_run_id, requirement_id, position) VALUES (?, ?, ?)",
                (run, rid, pos),
            )
    conn.commit()
    conn.close()


MAPPING = [
    {"id": "PRJ-1", "name": "Project one", "document_ids": ["DOC-A", "DOC-B", "DOC-C"]},
    {"id": "PRJ-2", "name": "Project two", "document_ids": ["DOC-C"]},
]


def counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {
            t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("documents", "source_passages", "requirements", "requirement_evidence",
                      "srs_runs", "srs_run_requirements")
        }
    finally:
        conn.close()


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "spectest.db"


# --------------------------------------------------------------- framework


class MigrationFrameworkTest(TempDirCase):
    def test_fresh_database_is_built_from_all_migrations(self) -> None:
        with SQLiteRepository(":memory:") as repo:
            st = migrate.status(repo.conn)
            self.assertTrue(st.up_to_date)
            self.assertEqual(st.current, st.latest)
            self.assertIn("projects", migrate.layout(repo.conn))
            self.assertIn("project_id", migrate.layout(repo.conn)["requirements"])
            self.assertEqual(repo.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_legacy_database_is_refused_until_migrated_and_keeps_its_data(self) -> None:
        build_legacy_db(self.db)
        before = counts(self.db)
        with self.assertRaisesRegex(RepositoryError, "migrate up"):
            SQLiteRepository(self.db)
        self.assertEqual(counts(self.db), before)

    def test_up_stamps_baseline_applies_002_and_preserves_data(self) -> None:
        build_legacy_db(self.db)
        before = counts(self.db)
        with mock.patch("builtins.print"):
            self.assertEqual(migrate.main(["up", "--db", str(self.db)]), 0)
        self.assertEqual(len(list(self.tmp.glob("spectest.db.bak-*"))), 1)
        self.assertEqual(counts(self.db), before)
        conn = sqlite3.connect(self.db)
        history = conn.execute("SELECT version, baseline FROM schema_migrations ORDER BY version").fetchall()
        self.assertEqual(history, [(1, 1), (2, 0)])
        self.assertEqual(conn.execute("SELECT count(*) FROM requirements WHERE project_id IS NOT NULL").fetchone()[0], 0)
        conn.close()
        with SQLiteRepository(self.db) as repo:  # now opens
            self.assertEqual(len(repo.list_requirements(unassigned=True)), 5)

    def test_backup_is_a_full_copy(self) -> None:
        build_legacy_db(self.db)
        copy = migrate.backup(self.db)
        self.assertEqual(counts(copy), counts(self.db))

    def test_unknown_old_layout_is_refused(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE requirements (id TEXT PRIMARY KEY, evidence_json TEXT)")
        conn.close()
        conn = sqlite3.connect(self.db)
        st = migrate.status(conn)
        self.assertTrue(st.problems)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate(conn)
        conn.close()

    def _custom_migrations(self, extra: dict[str, str]) -> Path:
        directory = self.tmp / "migrations"
        shutil.copytree(migrate.MIGRATIONS_DIR, directory)
        for name, sql in extra.items():
            (directory / name).write_text(sql, encoding="utf-8")
        return directory

    def test_failing_migration_leaves_no_trace(self) -> None:
        directory = self._custom_migrations({
            "003_broken.sql": "CREATE TABLE first_ok (x TEXT);\nALTER TABLE no_such_table ADD COLUMN y TEXT;\n"
        })
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = ON")
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            with self.assertRaisesRegex(migrate.MigrationError, "003_broken.sql failed"):
                migrate.migrate(conn)
            st = migrate.status(conn)
        self.assertEqual(st.current, 2)                       # 001 and 002 committed, 003 not
        self.assertNotIn("first_ok", migrate.layout(conn))    # first statement rolled back
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_edited_applied_migration_is_detected(self) -> None:
        directory = self._custom_migrations({})
        conn = sqlite3.connect(self.db)
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            migrate.migrate(conn)
            path = directory / "002_projects.sql"
            path.write_text(path.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
            st = migrate.status(conn)
        self.assertTrue(any("checksum" in p for p in st.problems))
        conn.close()

    def test_crlf_does_not_change_checksum(self) -> None:
        directory = self._custom_migrations({})
        path = directory / "002_projects.sql"
        lf = migrate.Migration(2, "projects", path).checksum
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.assertEqual(migrate.Migration(2, "projects", path).checksum, lf)

    def test_transaction_control_in_file_is_rejected(self) -> None:
        directory = self._custom_migrations({"003_bad.sql": "CREATE TABLE t (x);\nCOMMIT;\n"})
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            with self.assertRaisesRegex(migrate.MigrationError, "COMMIT"):
                migrate.discover()

    def test_trigger_body_is_allowed(self) -> None:
        directory = self._custom_migrations({
            "003_trigger.sql": "CREATE TRIGGER t AFTER INSERT ON projects\nBEGIN\n"
                               "  UPDATE projects SET description = 'x' WHERE id = NEW.id;\nEND;\n"
        })
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            conn = sqlite3.connect(":memory:")
            migrate.migrate(conn)
            self.assertEqual(migrate.status(conn).current, 3)
            conn.close()


# ----------------------------------------------------------------- backfill


class BackfillTest(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        build_legacy_db(self.db)
        conn = sqlite3.connect(self.db)
        migrate.migrate(conn)
        conn.close()
        self.repo = SQLiteRepository(self.db)
        self.addCleanup(self.repo.close)

    def test_apply_assigns_only_unambiguous_rows(self) -> None:
        report = project_backfill.apply(self.repo, MAPPING)
        assigned = {(d.kind, d.id): d.project_id for d in report.assigned}
        self.assertEqual(assigned, {("requirement", "RQ-1"): "PRJ-1", ("srs_run", "RUN-1"): "PRJ-1"})
        review = {d.id: d for d in report.needs_review}
        self.assertEqual(set(review), {"RQ-2", "RQ-3", "RQ-4", "RQ-5", "RUN-2"})
        # Multi-document goes to review even though both documents are in PRJ-1.
        self.assertIn("cites 2 documents", review["RQ-2"].reason)
        self.assertEqual(review["RQ-2"].candidate_project_ids, ["PRJ-1"])
        self.assertIn("no evidence", review["RQ-3"].reason)
        self.assertIn("shared by several projects", review["RQ-4"].reason)
        self.assertIn("not linked to any project", review["RQ-5"].reason)
        self.assertIn("RQ-2", review["RUN-2"].reason)
        self.assertEqual(report.documents_without_project, ["DOC-D"])
        self.assertEqual(report.scope_problems, [])
        self.assertIsNone(self.repo.get_requirement("RQ-2")["project_id"])

    def test_apply_is_idempotent(self) -> None:
        project_backfill.apply(self.repo, MAPPING)
        second = project_backfill.apply(self.repo, MAPPING)
        self.assertEqual(second.projects_created, [])
        self.assertEqual(second.links_added, 0)
        self.assertEqual(second.assigned, [])
        self.assertEqual({d.id for d in second.needs_review}, {"RQ-2", "RQ-3", "RQ-4", "RQ-5", "RUN-2"})

    def test_dry_run_changes_nothing(self) -> None:
        report = project_backfill.apply(self.repo, MAPPING, dry_run=True)
        self.assertEqual(len(report.assigned), 2)
        self.assertEqual(self.repo.list_projects(), [])
        self.assertEqual(len(self.repo.list_requirements(unassigned=True)), 5)

    def test_invalid_mapping_changes_nothing(self) -> None:
        bad = MAPPING + [{"id": "PRJ-3", "name": "Project three", "document_ids": ["DOC-NOPE"]}]
        with self.assertRaisesRegex(project_backfill.BackfillError, "DOC-NOPE"):
            project_backfill.apply(self.repo, bad)
        self.assertEqual(self.repo.list_projects(), [])

    def test_proposal_is_one_project_per_unlinked_document(self) -> None:
        proposal = project_backfill.propose_mapping(self.repo)["projects"]
        self.assertEqual([p["id"] for p in proposal], ["PRJ-A", "PRJ-B", "PRJ-C", "PRJ-D"])
        self.assertEqual(proposal[0]["document_ids"], ["DOC-A"])

    def test_reviewer_resolves_multi_document_requirement(self) -> None:
        mapping = [{"id": "PRJ-A", "name": "a", "document_ids": ["DOC-A"]},
                   {"id": "PRJ-B", "name": "b", "document_ids": ["DOC-B"]}]
        project_backfill.apply(self.repo, mapping)
        # DOC-B is not in PRJ-A: refused unless the reviewer confirms it belongs there.
        with self.assertRaisesRegex(RepositoryError, "DOC-B"):
            self.repo.set_requirement_project("RQ-2", "PRJ-A")
        self.assertIsNone(self.repo.get_requirement("RQ-2")["project_id"])
        self.repo.set_requirement_project("RQ-2", "PRJ-A", link_documents=True)
        self.assertEqual(self.repo.get_requirement("RQ-2")["project_id"], "PRJ-A")
        self.assertIn("DOC-B", self.repo.get_project("PRJ-A")["document_ids"])
        # Now RUN-2's requirements are all in PRJ-A.
        self.repo.set_srs_run_project("RUN-2", "PRJ-A")
        self.assertEqual(self.repo.get_srs_run("RUN-2")["project_id"], "PRJ-A")
        self.assertEqual({d.id for d in project_backfill.review(self.repo).needs_review},
                         {"RQ-3", "RQ-4", "RQ-5"})

    def test_cli_review_and_assign(self) -> None:
        project_backfill.apply(self.repo, MAPPING)
        self.repo.close()
        db = ["--db", str(self.db)]
        with mock.patch("builtins.print"):
            self.assertEqual(project_backfill.main([*db, "review"]), 1)
            self.assertEqual(project_backfill.main([*db, "assign", "RQ-2", "PRJ-2"]), 1)
            self.assertEqual(project_backfill.main([*db, "assign", "RQ-2", "PRJ-1"]), 0)
            self.assertEqual(project_backfill.main([*db, "assign-run", "RUN-2", "PRJ-1"]), 0)
        self.repo = SQLiteRepository(self.db)
        self.assertEqual(self.repo.get_requirement("RQ-2")["project_id"], "PRJ-1")


# -------------------------------------------------------- repository rules


class ProjectScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SQLiteRepository(":memory:")
        self.addCleanup(self.repo.close)
        r = self.repo
        self.doc_a = r.add_document("a.md", "ha", "a.md")
        self.doc_b = r.add_document("b.md", "hb", "b.md")
        self.pa = r.add_passage(self.doc_a, "Users shall log in.")
        self.pb = r.add_passage(self.doc_b, "Reports shall export.")
        self.prj = r.add_project("P", document_ids=[self.doc_a], project_id="PRJ-P")
        self.other = r.add_project("Q", document_ids=[self.doc_b], project_id="PRJ-Q")

    def ev(self, pid: str, quote: str) -> list[dict[str, str]]:
        return [{"passage_id": pid, "quote": quote}]

    def test_requirement_must_cite_only_its_project_documents(self) -> None:
        with self.assertRaisesRegex(RepositoryError, "not linked to project"):
            self.repo.add_requirement("functional", "s", **FR, project_id=self.prj,
                                      evidence=self.ev(self.pb, "Reports"), requirement_id="X")
        self.assertIsNone(self.repo.get_requirement("X"))
        rid = self.repo.add_requirement("functional", "s", **FR, project_id=self.prj,
                                        evidence=self.ev(self.pa, "log in"))
        with self.assertRaisesRegex(RepositoryError, "not linked to project"):
            self.repo.update_requirement(rid, evidence=self.ev(self.pb, "Reports"))
        self.assertEqual(self.repo.get_requirement(rid)["evidence"][0]["passage_id"], self.pa)
        self.assertEqual(Requirement.model_validate(self.repo.get_requirement(rid)).project_id, self.prj)

    def test_cited_document_cannot_be_unlinked_and_project_with_requirements_not_deleted(self) -> None:
        self.repo.add_requirement("functional", "s", **FR, project_id=self.prj,
                                  evidence=self.ev(self.pa, "log in"))
        with self.assertRaisesRegex(RepositoryError, "cited"):
            self.repo.unlink_document(self.prj, self.doc_a)
        with self.assertRaisesRegex(RepositoryError, "still owns"):
            self.repo.delete_project(self.prj)
        self.repo.delete_project(self.other)  # no requirements: allowed, links cascade
        self.assertEqual(self.repo.list_projects(document_id=self.doc_b), [])

    def test_srs_run_only_includes_its_project_requirements(self) -> None:
        rid = self.repo.add_requirement("functional", "s", **FR, project_id=self.prj,
                                        evidence=self.ev(self.pa, "log in"), status="approved")
        with self.assertRaisesRegex(RepositoryError, "another or no project"):
            self.repo.add_srs_run({}, [rid], project_id=self.other)
        run = self.repo.add_srs_run({}, [rid], project_id=self.prj)
        self.assertEqual(self.repo.list_srs_runs(project_id=self.prj)[0]["id"], run)
        self.assertEqual(self.repo.get_approved_requirements(self.other), [])
        # Moving the requirement out would break the run.
        self.repo.link_document(self.other, self.doc_a)
        with self.assertRaisesRegex(RepositoryError, "SRS runs of another project"):
            self.repo.set_requirement_project(rid, self.other)

    def test_existing_callers_without_project_still_work(self) -> None:
        rid = self.repo.add_requirement("functional", "s", **FR, evidence=self.ev(self.pb, "Reports"))
        self.repo.set_requirement_status(rid, "approved")
        self.repo.add_srs_run({}, [rid])
        self.assertEqual([r["id"] for r in self.repo.list_requirements(unassigned=True)], [rid])


if __name__ == "__main__":
    unittest.main()
