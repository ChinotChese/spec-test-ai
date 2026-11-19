"""SRS generator prompt: approved requirements + core context (CAG) + retrieved evidence.

Run from src/:  python -m unittest discover -s test -t .
No network: the model call is captured at gemini_client.generate_json and
retrieval is a fake function. Fixtures are synthetic.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import config
from agents import srs_generator
from context.core_context import CoreContextError, load_core_context
from database.sqlite_repository import SQLiteRepository
from prompts.srs_prompt import SYSTEM_PROMPT
from services import project_registry, requirement_registry
from test.test_project_pipeline import draft, fake_model_draft


class GeneratorContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = SQLiteRepository(":memory:")
        self.addCleanup(self.repo.close)
        r = self.repo
        self.doc_a = r.save_document_with_passages("DOC-A", "a.md", "ha", "a.md", [
            ("DOC-A-P0001", None, "Users log in with a password."),
            ("DOC-A-P0002", None, "The portal is used by sales staff."),
        ])
        self.doc_b = r.save_document_with_passages("DOC-B", "b.md", "hb", "b.md", [
            ("DOC-B-P0001", None, "The product replaces spreadsheets."),
            ("DOC-B-P0002", None, "It runs in the browser."),
        ])
        self.doc_x = r.save_document_with_passages("DOC-X", "x.md", "hx", "x.md", [
            ("DOC-X-P0001", None, "Another project's secret."),
        ])
        self.prj = project_registry.create("Alpha", document_ids=[self.doc_a, self.doc_b], repo=r)
        project_registry.create("Other", document_ids=[self.doc_x], repo=r)
        [self.rid] = requirement_registry.save_candidates(
            [draft("DOC-A-P0001")], project_id=self.prj, repo=r).saved_ids
        requirement_registry.approve(self.rid, repo=r)

        self.calls: list[dict] = []
        self.model_draft = None

    def fake_generate_json(self, client, **kw):
        self.calls.append(kw)
        if self.model_draft is not None:
            return json.dumps(self.model_draft)
        prompt = kw["contents"][0]
        features = [("Login", [{"id": self.rid}])]
        return json.dumps(fake_model_draft(features, [], [p for p in prompt]))

    def generate(self, hits=None, **kwargs):
        retrieve = kwargs.pop("retrieve", None) or (lambda project_id, queries: hits or {})
        with mock.patch("llm_clients.gemini_client.generate_json", side_effect=self.fake_generate_json):
            return srs_generator.generate_with_report([self.rid], repo=self.repo, retrieve=retrieve, **kwargs)

    @property
    def system(self) -> str:
        return self.calls[-1]["system_instruction"]

    @property
    def prompt(self) -> str:
        return self.calls[-1]["contents"][0]

    # ------------------------------------------------------------- CAG layout

    def test_system_instruction_is_rules_then_core_context(self) -> None:
        report = self.generate()
        core = load_core_context()
        self.assertTrue(self.system.startswith(SYSTEM_PROMPT))
        self.assertTrue(self.system.endswith(core.render()))
        self.assertEqual(report.core_context_version, core.version)

    def test_core_context_is_not_in_the_dynamic_part(self) -> None:
        self.generate()
        self.assertNotIn("<core_context", self.prompt)

    def test_requirements_come_before_evidence(self) -> None:
        self.generate()
        self.assertLess(self.prompt.index("<approved_requirements>"), self.prompt.index("<source_passages>"))

    def test_same_core_context_gives_identical_prefix(self) -> None:
        self.generate(hits={"purpose": ["DOC-B-P0001"]})
        first = self.system
        self.generate(hits={"operating_environment": ["DOC-B-P0002"]})
        self.assertEqual(self.system, first)

    def test_missing_core_context_stops_before_the_model(self) -> None:
        with mock.patch.object(srs_generator, "load_core_context", side_effect=CoreContextError("gone")):
            with self.assertRaisesRegex(srs_generator.GenerationError, "Core context unavailable"):
                self.generate()
        self.assertEqual(self.calls, [])

    # ------------------------------------------------------------ evidence

    def test_cited_passages_are_always_sent_and_marked(self) -> None:
        report = self.generate()
        self.assertIn('<passage id="DOC-A-P0001" cited="true">', self.prompt)
        self.assertEqual((report.evidence.mode, report.evidence.cited, report.evidence.retrieved),
                         ("retrieval", 1, 0))
        self.assertIn("retrieval found no passages", " ".join(report.warnings))

    def test_retrieved_passages_are_added_with_their_queries(self) -> None:
        report = self.generate(hits={"purpose": ["DOC-B-P0001", "DOC-A-P0001"],
                                     "product_scope": ["DOC-B-P0001"],
                                     "feature:Login": ["DOC-A-P0001"]})
        self.assertIn('<passage id="DOC-B-P0001" retrieved_for="purpose,product_scope">', self.prompt)
        self.assertIn('<passage id="DOC-A-P0001" cited="true" retrieved_for="purpose,feature:Login">',
                      self.prompt)
        self.assertNotIn("DOC-A-P0002", self.prompt)
        self.assertEqual((report.evidence.cited, report.evidence.retrieved, report.evidence.sent), (1, 1, 2))

    def test_queries_cover_every_narrative_section_and_feature(self) -> None:
        seen = {}
        self.generate(retrieve=lambda project_id, queries: seen.update(queries, project=project_id) or {})
        self.assertEqual(seen.pop("project"), self.prj)
        self.assertEqual(set(seen), set(srs_generator.NARRATIVE_QUERIES) | {"feature:Login"})
        self.assertIn("The system shall let users log in.", seen["feature:Login"])

    def test_passages_of_other_projects_or_deleted_are_ignored(self) -> None:
        report = self.generate(hits={"purpose": ["DOC-X-P0001", "DOC-GONE-P0001", "DOC-B-P0002"]})
        self.assertNotIn("DOC-X-P0001", self.prompt)
        self.assertNotIn("DOC-GONE-P0001", self.prompt)
        self.assertIn("DOC-B-P0002", self.prompt)
        self.assertIn("2 retrieved passages no longer exist in this project", " ".join(report.warnings))

    def test_budget_takes_best_hits_of_each_query_first(self) -> None:
        with mock.patch.object(config, "SRS_RETRIEVAL_MAX_PASSAGES", 2):
            report = self.generate(hits={"purpose": ["DOC-B-P0001", "DOC-A-P0002"],
                                         "product_scope": ["DOC-B-P0002", "DOC-A-P0002"]})
        self.assertEqual(report.evidence.retrieved, 2)
        self.assertIn("DOC-B-P0001", self.prompt)
        self.assertIn("DOC-B-P0002", self.prompt)
        self.assertNotIn("DOC-A-P0002", self.prompt)

    def test_retrieval_failure_falls_back_to_cited_documents(self) -> None:
        def broken(project_id, queries):
            raise ConnectionError("qdrant down")
        report = self.generate(retrieve=broken)
        self.assertEqual(report.evidence.mode, "fallback_all_passages")
        self.assertIn("DOC-A-P0002", self.prompt)          # whole cited document
        self.assertNotIn("DOC-B-P0001", self.prompt)       # uncited document: not sent
        self.assertIn("retrieval failed (ConnectionError: qdrant down)", " ".join(report.warnings))

    def test_retrieval_can_be_disabled(self) -> None:
        def must_not_run(project_id, queries):
            raise AssertionError("retrieval ran")
        report = self.generate(retrieve=must_not_run, retrieval=False)
        self.assertEqual((report.evidence.mode, report.evidence.sent), ("cited_only", 1))

    # ---------------------------------------------------------- references

    def test_references_include_documents_cited_by_narratives(self) -> None:
        features = [("Login", [{"id": self.rid}])]
        self.model_draft = fake_model_draft(features, [], [])
        self.model_draft["product_perspective"] = {
            "status": "specified", "text": "The product replaces spreadsheets.", "reason": None,
            "source_ids": ["DOC-B-P0001"]}
        report = self.generate(hits={"product_perspective": ["DOC-B-P0001"]})
        self.assertEqual(report.documents, [self.doc_a, self.doc_b])
        self.assertEqual([r.title for r in report.srs.introduction.references], ["a.md", "b.md"])

    def test_retrieved_but_uncited_documents_are_not_references(self) -> None:
        report = self.generate(hits={"purpose": ["DOC-B-P0001"]})
        self.assertEqual(report.documents, [self.doc_a])


if __name__ == "__main__":
    unittest.main()
