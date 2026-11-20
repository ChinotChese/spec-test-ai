"""SRS generator prompt: approved requirements + core context (CAG) + retrieved
evidence (level 1), and the explicit context cache (level 2).

Run from src/:  python -m unittest discover -s test -t .
No network: level-1 tests capture the model call at
gemini_client.generate_json_with_usage; cache tests run the real gemini_client
and context_cache code against a fake Gemini client. Retrieval is a fake
function. Fixtures are synthetic.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import config
from agents import srs_generator
from context.core_context import CoreContextError, load_core_context
from database.sqlite_repository import SQLiteRepository
from llm_clients import gemini_client
from prompts.srs_prompt import SYSTEM_PROMPT, build_system_instruction
from services import project_registry, requirement_registry
from test.test_project_pipeline import draft, fake_model_draft


class GeneratorFixture(unittest.TestCase):
    """Project, documents and one approved requirement; cache off by default."""

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
        patcher = mock.patch.object(config, "GEMINI_CONTEXT_CACHE", "off")
        patcher.start()
        self.addCleanup(patcher.stop)

    def draft_json(self) -> str:
        if self.model_draft is not None:
            return json.dumps(self.model_draft)
        return json.dumps(fake_model_draft([("Login", [{"id": self.rid}])], [], []))

    def fake_generate_json(self, client, **kw):
        self.calls.append(kw)
        return gemini_client.GeminiResult(self.draft_json(), gemini_client.Usage(100, 0, 10))

    def generate(self, hits=None, **kwargs):
        retrieve = kwargs.pop("retrieve", None) or (lambda project_id, queries: hits or {})
        with mock.patch("llm_clients.gemini_client.generate_json_with_usage",
                        side_effect=self.fake_generate_json):
            return srs_generator.generate_with_report([self.rid], repo=self.repo, retrieve=retrieve, **kwargs)

    @property
    def system(self) -> str:
        return self.calls[-1]["system_instruction"]

    @property
    def prompt(self) -> str:
        return self.calls[-1]["contents"][0]


class GeneratorContextTest(GeneratorFixture):
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


# ---------------------------------------------------------------- level 2


class FakeGemini:
    """Just enough of google.genai.Client: caches.* and models.generate_content."""

    CACHED = 7000

    def __init__(self, test: "ExplicitCacheTest") -> None:
        self.test = test
        self.store: dict[str, SimpleNamespace] = {}
        self.requests: list[dict] = []
        self.create_calls = 0
        self.fail_create: Exception | None = None
        self.expire_on_use = False
        self.caches = SimpleNamespace(list=self._list, create=self._create,
                                      update=self._update, delete=self._delete)
        self.models = SimpleNamespace(generate_content=self._generate)

    @staticmethod
    def _ttl(config) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(config.ttl.rstrip("s")))

    def _list(self):
        return list(self.store.values())

    def _create(self, *, model, config):
        self.create_calls += 1
        if self.fail_create:
            raise self.fail_create
        name = f"cachedContents/c{self.create_calls}"
        self.store[name] = SimpleNamespace(
            name=name, display_name=config.display_name, model=f"models/{model}",
            system_instruction=config.system_instruction, expire_time=self._ttl(config),
            usage_metadata=SimpleNamespace(total_token_count=self.CACHED))
        return self.store[name]

    def _update(self, *, name, config):
        self.store[name].expire_time = self._ttl(config)
        return self.store[name]

    def _delete(self, *, name):
        self.store.pop(name, None)

    def _generate(self, *, model, contents, config):
        self.requests.append(config)
        cached = config.get("cached_content")
        if cached and self.expire_on_use:
            self.store.clear()
        if cached and cached not in self.store:
            from google.genai import errors
            raise errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                     "message": "CachedContent not found (or permission denied)"}})
        return SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
            text=self.test.draft_json(),
            usage_metadata=SimpleNamespace(prompt_token_count=self.CACHED + 500,
                                           cached_content_token_count=self.CACHED if cached else None,
                                           candidates_token_count=300))


class ExplicitCacheTest(GeneratorFixture):
    def setUp(self) -> None:
        super().setUp()
        self.gemini = FakeGemini(self)

    def run_cached(self, **kwargs):
        return srs_generator.generate_with_report(
            [self.rid], repo=self.repo, retrieve=lambda project_id, queries: {},
            client=self.gemini, cache=True, **kwargs)

    def test_cache_holds_the_level_1_prefix_and_requests_omit_it(self) -> None:
        report = self.run_cached()
        [cache] = self.gemini.store.values()
        self.assertEqual(cache.system_instruction, build_system_instruction(load_core_context().render()))
        [request] = self.gemini.requests
        self.assertEqual(request["cached_content"], cache.name)
        self.assertNotIn("system_instruction", request)
        self.assertEqual((report.cache.mode, report.cache.created, report.cache.tokens),
                         ("explicit", True, FakeGemini.CACHED))

    def test_cache_is_named_after_the_core_context_version(self) -> None:
        report = self.run_cached()
        self.assertTrue(report.cache.display_name.startswith(f"spectest-{load_core_context().version}-"))

    def test_second_run_reuses_the_cache(self) -> None:
        first = self.run_cached()
        second = self.run_cached()
        self.assertEqual(self.gemini.create_calls, 1)
        self.assertFalse(second.cache.created)
        self.assertEqual(second.cache.name, first.cache.name)

    def test_usage_reports_cached_tokens_from_usage_metadata(self) -> None:
        report = self.run_cached()
        [usage] = report.usage
        self.assertEqual((usage.prompt_tokens, usage.cached_tokens, usage.output_tokens),
                         (FakeGemini.CACHED + 500, FakeGemini.CACHED, 300))
        self.assertEqual(report.cached_tokens, FakeGemini.CACHED)
        self.assertAlmostEqual(usage.cached_ratio, FakeGemini.CACHED / (FakeGemini.CACHED + 500))

    def test_a_new_core_version_gets_a_new_cache(self) -> None:
        self.run_cached()
        core = load_core_context()
        changed = srs_generator.load_core_context()
        with mock.patch.object(srs_generator, "load_core_context",
                               return_value=type(core)(version="core-0000000000000000", sources=changed.sources)):
            report = self.run_cached()
        self.assertEqual(self.gemini.create_calls, 2)
        self.assertIn("core-0000000000000000", report.cache.display_name)

    def test_creation_failure_falls_back_to_the_full_prompt(self) -> None:
        self.gemini.fail_create = RuntimeError("caching not available")
        report = self.run_cached()
        [request] = self.gemini.requests
        self.assertNotIn("cached_content", request)
        self.assertTrue(request["system_instruction"].startswith(SYSTEM_PROMPT))
        self.assertEqual(report.cache.mode, "unavailable")
        self.assertIn("explicit context cache unavailable", " ".join(report.warnings))

    def test_cache_expiring_mid_run_repeats_the_call_with_the_full_prompt(self) -> None:
        self.gemini.expire_on_use = True
        report = self.run_cached()
        first, second = self.gemini.requests
        self.assertIn("cached_content", first)
        self.assertNotIn("cached_content", second)
        self.assertTrue(second["system_instruction"].startswith(SYSTEM_PROMPT))
        self.assertTrue(report.cache.expired_mid_run)
        self.assertIsNone(report.usage[0].cached_content)
        self.assertIn("expired during the run", " ".join(report.warnings))

    def test_cache_off_never_touches_caches(self) -> None:
        self.gemini.caches = None  # any use would fail
        report = srs_generator.generate_with_report(
            [self.rid], repo=self.repo, retrieve=lambda project_id, queries: {},
            client=self.gemini, cache=False)
        self.assertEqual(report.cache.mode, "off")
        self.assertIn("system_instruction", self.gemini.requests[0])


if __name__ == "__main__":
    unittest.main()
