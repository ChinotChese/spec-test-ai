"""Migration framework, 002_projects, 003_project_id_not_null, project-scope rules.

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

from database import migrate
from database.sqlite_repository import RepositoryError, SQLiteRepository
from schemas.requirement import Requirement

FR = {"priority": "must", "feature": "Login"}
LATEST = len(migrate.discover())


def build_legacy_db(path: Path, *, with_requirements: bool) -> None:
    """Pre-framework database (001 layout, no history).

    Always: documents A, B with one passage each.
    with_requirements: RQ-1 (evidence A), RQ-2 (evidence A + B), and SRS run
    RUN-1 [RQ-1, RQ-2]; none of them has a project, as before 002.
    """
    conn = sqlite3.connect(path)
    conn.executescript(migrate.discover()[0].sql)
    for doc in "AB":
        conn.execute(
            "INSERT INTO documents (id, filename, file_hash, file_path) VALUES (?, ?, ?, ?)",
            (f"DOC-{doc}", f"doc_{doc}.md", f"hash-{doc}", f"input/doc_{doc}.md"),
        )
        conn.execute(
            "INSERT INTO source_passages (id, document_id, text) VALUES (?, ?, ?)",
            (f"P-{doc}", f"DOC-{doc}", f"The system shall do thing {doc}."),
        )
    if with_requirements:
        for rid, docs in [("RQ-1", "A"), ("RQ-2", "AB")]:
            conn.execute(
                "INSERT INTO requirements (id, type, statement, status, priority, feature) "
                "VALUES (?, 'functional', ?, 'approved', 'must', 'Login')",
                (rid, f"The system shall {rid}."),
            )
            for doc in docs:
                conn.execute(
                    "INSERT INTO requirement_evidence (requirement_id, passage_id, quote) VALUES (?, ?, ?)",
                    (rid, f"P-{doc}", f"do thing {doc}"),
                )
        conn.execute("INSERT INTO srs_runs (id, srs_json) VALUES ('RUN-1', '{}')")
        conn.executemany(
            "INSERT INTO srs_run_requirements (srs_run_id, requirement_id, position) VALUES ('RUN-1', ?, ?)",
            [("RQ-1", 0), ("RQ-2", 1)],
        )
    conn.commit()
    conn.close()


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


def version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return migrate.status(conn).current
    finally:
        conn.close()


def up(path: Path) -> int:
    with mock.patch("builtins.print"):
        return migrate.main(["up", "--db", str(path), "--no-backup"])


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
            self.assertEqual(st.current, LATEST)
            self.assertIn("projects", migrate.layout(repo.conn))
            self.assertIn("project_id", migrate.layout(repo.conn)["requirements"])
            self.assertEqual(repo.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_legacy_database_is_refused_until_migrated_and_keeps_its_data(self) -> None:
        build_legacy_db(self.db, with_requirements=True)
        before = counts(self.db)
        with self.assertRaisesRegex(RepositoryError, "migrate up"):
            SQLiteRepository(self.db)
        self.assertEqual(counts(self.db), before)

    def test_up_takes_a_backup_stamps_baseline_and_keeps_data(self) -> None:
        build_legacy_db(self.db, with_requirements=False)  # like the real DB: documents only
        before = counts(self.db)
        with mock.patch("builtins.print"):
            self.assertEqual(migrate.main(["up", "--db", str(self.db)]), 0)
        self.assertEqual(len(list(self.tmp.glob("spectest.db.bak-*"))), 1)
        self.assertEqual(counts(self.db), before)
        conn = sqlite3.connect(self.db)
        history = conn.execute("SELECT version, baseline FROM schema_migrations ORDER BY version").fetchall()
        conn.close()
        self.assertEqual(history, [(1, 1)] + [(v, 0) for v in range(2, LATEST + 1)])
        SQLiteRepository(self.db).close()  # now opens

    def test_backup_is_a_full_copy(self) -> None:
        build_legacy_db(self.db, with_requirements=True)
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
        name = f"{LATEST + 1:03d}_broken.sql"
        directory = self._custom_migrations({
            name: "CREATE TABLE first_ok (x TEXT);\nALTER TABLE no_such_table ADD COLUMN y TEXT;\n"
        })
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = ON")
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            with self.assertRaisesRegex(migrate.MigrationError, f"{name} failed"):
                migrate.migrate(conn)
            st = migrate.status(conn)
        self.assertEqual(st.current, LATEST)                  # real ones committed, broken one not
        self.assertNotIn("first_ok", migrate.layout(conn))    # its first statement rolled back
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
        directory = self._custom_migrations({f"{LATEST + 1:03d}_bad.sql": "CREATE TABLE t (x);\nCOMMIT;\n"})
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            with self.assertRaisesRegex(migrate.MigrationError, "COMMIT"):
                migrate.discover()

    def test_trigger_body_is_allowed(self) -> None:
        directory = self._custom_migrations({
            f"{LATEST + 1:03d}_trigger.sql": "CREATE TRIGGER t AFTER INSERT ON projects\nBEGIN\n"
                                             "  UPDATE projects SET description = 'x' WHERE id = NEW.id;\nEND;\n"
        })
        with mock.patch.object(migrate, "MIGRATIONS_DIR", directory):
            conn = sqlite3.connect(":memory:")
            migrate.migrate(conn)
            self.assertEqual(migrate.status(conn).current, LATEST + 1)
            conn.close()


# ------------------------------------------------- 003_project_id_not_null


class ProjectIdNotNullTest(TempDirCase):
    def test_rows_without_project_stop_003_and_nothing_is_lost(self) -> None:
        build_legacy_db(self.db, with_requirements=True)
        before = counts(self.db)
        self.assertEqual(up(self.db), 1)
        self.assertEqual(version(self.db), 2)                 # 002 applied, 003 rolled back
        self.assertEqual(counts(self.db), before)
        conn = sqlite3.connect(self.db)
        self.assertNotIn("requirements_new", migrate.layout(conn))
        conn.close()

    def test_003_succeeds_once_rows_have_a_project_and_keeps_links_and_order(self) -> None:
        build_legacy_db(self.db, with_requirements=True)
        up(self.db)                                           # stops at 2
        conn = sqlite3.connect(self.db)                       # fix the data by hand
        conn.execute("INSERT INTO projects (id, name) VALUES ('PRJ-1', 'One')")
        conn.executemany("INSERT INTO project_documents (project_id, document_id) VALUES ('PRJ-1', ?)",
                         [("DOC-A",), ("DOC-B",)])
        conn.execute("UPDATE requirements SET project_id = 'PRJ-1'")
        conn.execute("UPDATE srs_runs SET project_id = 'PRJ-1'")
        conn.commit()
        conn.close()
        before = counts(self.db)

        self.assertEqual(up(self.db), 0)
        self.assertEqual(version(self.db), LATEST)
        self.assertEqual(counts(self.db), before)
        with SQLiteRepository(self.db) as repo:
            self.assertEqual([r["id"] for r in repo.list_requirements()], ["RQ-1", "RQ-2"])
            self.assertEqual(repo.get_requirement("RQ-2")["evidence"][1]["passage_id"], "P-B")
            self.assertEqual(repo.get_srs_run("RUN-1")["requirement_ids"], ["RQ-1", "RQ-2"])
            conn = repo.conn
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            targets = {r[2] for t in ("requirement_evidence", "srs_run_requirements")
                       for r in conn.execute(f"PRAGMA foreign_key_list({t})")}
            self.assertEqual(targets, {"requirements", "source_passages", "srs_runs"})
            with self.assertRaises(sqlite3.IntegrityError):    # NOT NULL is enforced
                conn.execute("INSERT INTO requirements (id, project_id, type, statement, priority, feature) "
                             "VALUES ('X', NULL, 'functional', 's', 'must', 'f')")
            with self.assertRaisesRegex(RepositoryError, "SRS run"):  # FK from srs_run_requirements still works
                repo.delete_requirement("RQ-1")
            index_names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            self.assertLessEqual({"idx_requirements_status", "idx_requirements_project",
                                  "idx_srs_runs_project"}, index_names)


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
        with self.assertRaisesRegex(RepositoryError, "another project"):
            self.repo.add_srs_run({}, [rid], project_id=self.other)
        run = self.repo.add_srs_run({}, [rid], project_id=self.prj)
        self.assertEqual(self.repo.list_srs_runs(project_id=self.prj)[0]["id"], run)
        self.assertEqual(self.repo.get_approved_requirements(self.other), [])
        # Moving the requirement out would break the run.
        self.repo.link_document(self.other, self.doc_a)
        with self.assertRaisesRegex(RepositoryError, "SRS runs of another project"):
            self.repo.set_requirement_project(rid, self.other)

    def test_move_requirement_to_another_project(self) -> None:
        rid = self.repo.add_requirement("functional", "s", **FR, project_id=self.prj,
                                        evidence=self.ev(self.pa, "log in"))
        with self.assertRaisesRegex(RepositoryError, "not linked to project"):
            self.repo.set_requirement_project(rid, self.other)   # doc A is not in Q
        self.assertEqual(self.repo.get_requirement(rid)["project_id"], self.prj)
        self.repo.set_requirement_project(rid, self.other, link_documents=True)
        self.assertEqual(self.repo.get_requirement(rid)["project_id"], self.other)
        self.assertIn(self.doc_a, self.repo.get_project(self.other)["document_ids"])
        with self.assertRaisesRegex(RepositoryError, "project_id is required"):
            self.repo.set_requirement_project(rid, None)

    def test_requirement_without_project_is_refused(self) -> None:
        with self.assertRaisesRegex(RepositoryError, "project_id is required"):
            self.repo.add_requirement("functional", "s", **FR, project_id=None,
                                      evidence=self.ev(self.pb, "Reports"))
        with self.assertRaises(TypeError):  # the keyword itself is required
            self.repo.add_requirement("functional", "s", **FR, evidence=self.ev(self.pb, "Reports"))
        self.assertEqual(self.repo.list_requirements(), [])


if __name__ == "__main__":
    unittest.main()
