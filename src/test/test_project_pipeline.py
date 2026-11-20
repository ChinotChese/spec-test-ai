"""The pipeline is project-aware: ingest, registry, generator, validator, exporter.

Run from src/:  python -m unittest discover -s test -t .
No Gemini calls: ingest uses the already-stored path, the generator's model
call is replaced by a fixed draft. Fixtures are synthetic.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from agents import srs_generator
from database.sqlite_repository import RepositoryError, SQLiteRepository
from services import document_loader, project_registry, requirement_registry, srs_validator

STATEMENT = "The system shall let users log in."


def draft(passage_id: str, statement: str = STATEMENT) -> dict:
    return {"type": "functional", "statement": statement, "priority": "must", "feature": "Login",
            "evidence": [{"passage_id": passage_id, "quote": "users log in"}]}


def fake_model_draft(features, nonfunctional, passages) -> dict:
    """A minimal valid model answer: no narratives, one sequence per feature."""
    return {
        **{name: {"status": "unresolved", "text": None, "reason": "Not stated.", "source_ids": []}
           for name in srs_generator.NARRATIVE_SECTIONS},
        "features": [
            {"feature": name, "description": f"{name} feature.",
             "sequences": [{"stimulus_or_condition": "User acts", "response": "System responds",
                            "requirement_ids": [reqs[0]["id"]]}]}
            for name, reqs in features
        ],
    }


def setUpModule() -> None:
    # The generator retrieves evidence from Qdrant (network + Gemini embeddings)
    # and creates a Gemini context cache by default; these tests must stay offline.
    for patcher in (mock.patch.object(srs_generator, "_retrieve", return_value={}),
                    mock.patch.object(config, "GEMINI_CONTEXT_CACHE", "off")):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


class PipelineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SQLiteRepository(":memory:")
        self.addCleanup(self.repo.close)
        r = self.repo
        self.doc_a = r.add_document("a.md", "ha", "a.md")
        self.doc_b = r.add_document("b.md", "hb", "b.md")
        self.pa = r.add_passage(self.doc_a, "Users log in with a password.")
        self.pb = r.add_passage(self.doc_b, "Users log in with a badge.")
        self.prj_a = project_registry.create("Alpha", document_ids=[self.doc_a], repo=r)
        self.prj_b = project_registry.create("Beta", document_ids=[self.doc_b], repo=r)

    def save_and_approve(self, project_id: str, passage_id: str, statement: str = STATEMENT) -> str:
        [rid] = requirement_registry.save_candidates(
            [draft(passage_id, statement)], project_id=project_id, repo=self.repo
        ).saved_ids
        requirement_registry.approve(rid, repo=self.repo)
        return rid


class RepositoryRuleTest(PipelineCase):
    def test_new_requirements_and_runs_need_a_project(self) -> None:
        with self.assertRaisesRegex(RepositoryError, "project_id is required"):
            self.repo.add_requirements_with_sequential_ids([draft(self.pa)], prefix="X-")
        with self.assertRaisesRegex(RepositoryError, "not found"):
            self.repo.add_requirement(**draft(self.pa), project_id="PRJ-nope")
        self.assertEqual(self.repo.list_requirements(), [])
        rid = self.save_and_approve(self.prj_a, self.pa)
        with self.assertRaisesRegex(RepositoryError, "project_id is required"):
            self.repo.add_srs_run({}, [rid], project_id=None)

    def test_rename_project(self) -> None:
        self.repo.update_project(self.prj_a, name="Alpha 2")
        self.assertEqual(self.repo.get_project(self.prj_a)["name"], "Alpha 2")
        with self.assertRaisesRegex(RepositoryError, "UNIQUE"):
            self.repo.update_project(self.prj_a, name="Beta")


class IngestTest(PipelineCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_document_is_linked_in_the_same_transaction(self) -> None:
        doc = self.repo.save_document_with_passages(
            "DOC-c", "c.md", "hc", "c.md", [("DOC-c-P0001", None, "Text.")], project_id=self.prj_a
        )
        self.assertIn(doc, self.repo.get_project(self.prj_a)["document_ids"])
        with self.assertRaises(RepositoryError):
            self.repo.save_document_with_passages(
                "DOC-d", "d.md", "hd", "d.md", [("DOC-d-P0001", None, "Text.")], project_id="PRJ-nope"
            )
        self.assertIsNone(self.repo.get_document("DOC-d"))  # rolled back with the link

    def test_already_stored_file_is_linked_to_a_second_project(self) -> None:
        path = self.tmp / "spec.md"
        path.write_bytes(b"Users shall log in.")
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        doc = self.repo.save_document_with_passages(
            "DOC-x", "spec.md", file_hash, "spec.md", [("DOC-x-P0001", None, "Users shall log in.")],
            project_id=self.prj_a,
        )
        passages = document_loader.ingest([path], project_id=self.prj_b, repo=self.repo)  # no model call
        self.assertEqual([p["id"] for p in passages], ["DOC-x-P0001"])
        self.assertEqual([p["id"] for p in self.repo.list_projects(document_id=doc)],
                         [self.prj_a, self.prj_b])

    def test_unknown_project_fails_before_any_model_call(self) -> None:
        path = self.tmp / "new.md"
        path.write_text("New text.", encoding="utf-8")
        with mock.patch.object(document_loader, "_call_gemini") as call:
            with self.assertRaisesRegex(document_loader.DocumentLoaderError, "not found"):
                document_loader.ingest([path], project_id="PRJ-nope", repo=self.repo)
            call.assert_not_called()


class RegistryTest(PipelineCase):
    def test_candidate_citing_another_projects_document_is_refused(self) -> None:
        with self.assertRaisesRegex(requirement_registry.RegistryError, "outside project"):
            requirement_registry.save_candidates([draft(self.pa), draft(self.pb, "Other.")],
                                                 project_id=self.prj_a, repo=self.repo)
        self.assertEqual(self.repo.list_requirements(), [])  # nothing saved

    def test_unknown_project_is_refused(self) -> None:
        with self.assertRaisesRegex(requirement_registry.RegistryError, "not found"):
            requirement_registry.save_candidates([draft(self.pa)], project_id="PRJ-nope", repo=self.repo)
        with self.assertRaisesRegex(requirement_registry.RegistryError, "not found"):
            requirement_registry.get_approved(project_id="PRJ-nope", repo=self.repo)

    def test_duplicates_are_detected_per_project(self) -> None:
        a = requirement_registry.save_candidates([draft(self.pa)], project_id=self.prj_a, repo=self.repo)
        b = requirement_registry.save_candidates([draft(self.pb)], project_id=self.prj_b, repo=self.repo)
        again = requirement_registry.save_candidates([draft(self.pa)], project_id=self.prj_a, repo=self.repo)
        self.assertEqual(len(a.saved_ids), 1)
        self.assertEqual(len(b.saved_ids), 1)  # same statement, other project: kept
        self.assertEqual(again.saved_ids, [])
        self.assertEqual(again.duplicates, [(STATEMENT, a.saved_ids[0])])

    def test_get_approved_returns_one_project(self) -> None:
        ra = self.save_and_approve(self.prj_a, self.pa)
        self.save_and_approve(self.prj_b, self.pb)
        approved = requirement_registry.get_approved(project_id=self.prj_a, repo=self.repo)
        self.assertEqual([r.id for r in approved], [ra])
        self.assertEqual(approved[0].project_id, self.prj_a)


class GenerateAndValidateTest(PipelineCase):
    def generate(self, requirements):
        with mock.patch.object(srs_generator, "_ask_model",
                               side_effect=lambda c, m, f, n, p, fb, **kw: (fake_model_draft(f, n, p), None)):
            return srs_generator.generate_with_report(requirements, repo=self.repo)

    def test_generator_refuses_mixed_projects_before_the_model(self) -> None:
        ra = self.save_and_approve(self.prj_a, self.pa)
        rb = self.save_and_approve(self.prj_b, self.pb, "The system shall accept badges.")
        with mock.patch.object(srs_generator, "_ask_model") as ask:
            with self.assertRaisesRegex(srs_generator.GenerationError, "several projects"):
                srs_generator.generate_with_report([ra, rb], repo=self.repo)
            ask.assert_not_called()

    def test_database_rejects_a_requirement_without_project(self) -> None:
        rid = self.save_and_approve(self.prj_a, self.pa)
        with self.assertRaisesRegex(Exception, "NOT NULL"):
            self.repo.conn.execute("UPDATE requirements SET project_id = NULL WHERE id = ?", (rid,))

    def test_same_statement_in_two_projects_validates_per_project(self) -> None:
        ra = self.save_and_approve(self.prj_a, self.pa)
        rb = self.save_and_approve(self.prj_b, self.pb)  # identical statement in Beta
        report = self.generate([ra])
        self.assertEqual(report.project_id, self.prj_a)
        self.assertEqual(report.srs.document_control.project_name, "Alpha")
        result = srs_validator.check(report.srs, [ra], requirement_map=report.requirement_map,
                                     repo=self.repo)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.project_id, self.prj_a)
        self.assertEqual(result.requirement_map, {"REQ-1.1": ra})
        # Validated against Beta, the SRS's evidence does not match Beta's requirement.
        other = srs_validator.check(report.srs, project_id=self.prj_b, repo=self.repo)
        self.assertTrue(any("source_ids" in e for e in other.errors), other.errors)
        self.assertNotIn(rb, [e for e in result.requirement_map.values()])

    def test_validator_needs_a_single_project(self) -> None:
        ra = self.save_and_approve(self.prj_a, self.pa)
        rb = self.save_and_approve(self.prj_b, self.pb, "The system shall accept badges.")
        srs = self.generate([ra]).srs
        self.assertIn("span several projects", srs_validator.check(srs, [ra, rb], repo=self.repo).errors[0])
        self.assertIn("cannot tell", srs_validator.check(srs, repo=self.repo).errors[0])
        self.assertIn("not in project", srs_validator.check(srs, [ra], project_id=self.prj_b,
                                                            repo=self.repo).errors[0])


class AppEndToEndTest(unittest.TestCase):
    """app.py steps 1-7 on a temp database, with config paths patched."""

    def test_pipeline_runs_for_one_project(self) -> None:
        import app
        import config
        from agents import requirement_extractor
        from schemas.requirement import RequirementDraft

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db, out = tmp / "spectest.db", tmp / "output"
        source = tmp / "spec.md"
        source.write_bytes(b"Users log in with a password.")
        with SQLiteRepository(db) as repo:  # stored earlier: ingest takes the no-model path
            prj = project_registry.create("Gamma Portal", repo=repo)
            repo.save_document_with_passages(
                "DOC-g", "spec.md", hashlib.sha256(source.read_bytes()).hexdigest(), "spec.md",
                [("DOC-g-P0001", None, "Users log in with a password.")],
            )
        fake_extract = lambda passages: [RequirementDraft.model_validate(draft(passages[0]["id"]))]
        with mock.patch.object(config, "DB_PATH", db), mock.patch.object(config, "OUTPUT_DIR", out), \
                mock.patch.object(requirement_extractor, "extract", side_effect=fake_extract), \
                mock.patch.object(srs_generator, "_ask_model",
                                  side_effect=lambda c, m, f, n, p, fb, **kw: (fake_model_draft(f, n, p), None)):
            saved = app.generate_srs_pipeline([source], prj)
            self.assertEqual(len(saved), 1)
            [row] = requirement_registry.list_by_status("pending", project_id=prj)
            requirement_registry.approve(row["id"])
            path = app.generate_approved_srs(prj)
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, out)
        self.assertIn("Gamma-Portal", path.name)
        text = path.read_text(encoding="utf-8")
        self.assertIn(STATEMENT, text)
        self.assertIn(row["id"], text)
        with SQLiteRepository(db) as repo:
            self.assertEqual(repo.get_requirement(row["id"])["project_id"], prj)
            self.assertEqual(repo.list_projects(document_id="DOC-g")[0]["id"], prj)


if __name__ == "__main__":
    unittest.main()
