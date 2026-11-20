"""retrieval.indexer: SQLite passages -> Qdrant, never indexed twice.

Run from src/:  python -m unittest discover -s test -t .
No network: Qdrant runs in local in-memory mode, Gemini is a fake client
that returns a deterministic vector per text and counts what it was sent.
Fixtures are synthetic.
"""

from __future__ import annotations

import hashlib
import unittest
import warnings
from types import SimpleNamespace
from unittest import mock

import config
from database.sqlite_repository import SQLiteRepository

try:
    from qdrant_client import QdrantClient, models
except ImportError:  # pragma: no cover
    QdrantClient = None

DIM = 8
COLLECTION = "source_passages_test"


class FakeEmbedClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.models = self

    def embed_content(self, *, model, contents, config):
        self.calls.append(list(contents))
        assert config["output_dimensionality"] == DIM
        return SimpleNamespace(embeddings=[SimpleNamespace(values=_vector(t)) for t in contents])

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 + 0.01 for b in digest[:DIM]]


@unittest.skipIf(QdrantClient is None, "qdrant-client is not installed")
class PassageIndexerTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.multiple(config, GEMINI_EMBEDDING_DIM=DIM, GEMINI_EMBEDDING_BATCH_SIZE=2,
                                      GEMINI_MODEL_EMBEDDING="fake-embedding")
        patcher.start()
        self.addCleanup(patcher.stop)
        warnings_cm = warnings.catch_warnings()
        warnings_cm.__enter__()
        self.addCleanup(warnings_cm.__exit__, None, None, None)
        warnings.filterwarnings("ignore", "Payload indexes have no effect", UserWarning)
        from retrieval import indexer, qdrant_store, retriever, sparse
        self.indexer, self.store, self.retriever, self.sparse = indexer, qdrant_store, retriever, sparse

        self.repo = SQLiteRepository(":memory:")
        self.addCleanup(self.repo.close)
        self.qdrant = QdrantClient(":memory:")
        self.addCleanup(self.qdrant.close)
        self.gemini = FakeEmbedClient()

        self.doc_a = self.repo.save_document_with_passages(
            "DOC-A", "a.pdf", "hash-a", "input/a.pdf",
            [("DOC-A-P0001", 1, "The system shall let users log in."),
             ("DOC-A-P0002", 1, "Passwords expire after 90 days."),
             ("DOC-A-P0003", 2, "Reports export to CSV.")],
        )
        self.doc_b = self.repo.save_document_with_passages(
            "DOC-B", "b.pdf", "hash-b", "input/b.pdf",
            [("DOC-B-P0001", 1, "Passwords expire after 90 days."),   # same text as DOC-A-P0002
             ("DOC-B-P0002", 1, "Audit logs are kept for one year.")],
        )
        self.p1 = self.repo.add_project("Alpha", document_ids=[self.doc_a], project_id="PRJ-1")
        self.p2 = self.repo.add_project("Beta", document_ids=[self.doc_b], project_id="PRJ-2")

    def run_index(self, **kwargs):
        return self.indexer.index_passages(repo=self.repo, qdrant=self.qdrant, embed_client=self.gemini,
                                           collection=COLLECTION, **kwargs)

    def count(self) -> int:
        return self.qdrant.count(COLLECTION, exact=True).count

    def payload(self, passage_id: str) -> dict:
        [record] = self.qdrant.retrieve(COLLECTION, ids=[self.store.point_id(passage_id)])
        return record.payload

    def test_first_run_embeds_each_distinct_text_once(self) -> None:
        report = self.run_index()
        self.assertEqual(self.count(), 5)
        self.assertEqual(sorted(self.gemini.texts), sorted({
            "The system shall let users log in.", "Passwords expire after 90 days.",
            "Reports export to CSV.", "Audit logs are kept for one year.",
        }))
        self.assertEqual((report.embedded, report.reused, report.embedding_calls), (4, 1, 4))
        self.assertEqual(self.payload("DOC-B-P0001")["project_ids"], ["PRJ-2"])

    def test_second_run_writes_nothing(self) -> None:
        self.run_index()
        self.gemini.calls.clear()
        report = self.run_index()
        self.assertEqual(self.gemini.calls, [])
        self.assertEqual((report.embedded, report.reused, report.payload_updated, report.unchanged),
                         (0, 0, 0, 5))
        self.assertEqual(self.count(), 5)

    def test_changed_text_is_re_embedded_in_place(self) -> None:
        self.run_index()
        self.gemini.calls.clear()
        with self.repo.conn:
            self.repo.conn.execute("UPDATE source_passages SET text = ? WHERE id = ?",
                                   ("Reports export to CSV and XLSX.", "DOC-A-P0003"))
        report = self.run_index()
        self.assertEqual(self.gemini.texts, ["Reports export to CSV and XLSX."])
        self.assertEqual(report.embedded, 1)
        self.assertEqual(self.count(), 5)
        self.assertEqual(self.payload("DOC-A-P0003")["text"], "Reports export to CSV and XLSX.")

    def test_new_link_updates_payload_without_embedding(self) -> None:
        self.run_index()
        self.gemini.calls.clear()
        self.repo.link_document(self.p1, self.doc_b)
        report = self.run_index()
        self.assertEqual(self.gemini.calls, [])
        self.assertEqual(report.payload_updated, 2)
        self.assertEqual(self.payload("DOC-B-P0002")["project_ids"], ["PRJ-1", "PRJ-2"])

    def test_project_scope_only_embeds_that_project(self) -> None:
        report = self.run_index(project_id=self.p2)
        self.assertEqual(report.in_scope, 2)
        self.assertEqual(self.count(), 2)
        self.assertEqual(sorted(self.gemini.texts),
                         ["Audit logs are kept for one year.", "Passwords expire after 90 days."])
        self.gemini.calls.clear()
        report = self.run_index()  # the other project's duplicate text reuses the stored vector
        self.assertEqual(sorted(self.gemini.texts), ["Reports export to CSV.",
                                                     "The system shall let users log in."])
        self.assertEqual(report.reused, 1)
        self.assertEqual(self.count(), 5)

    def test_unlinked_document_is_skipped(self) -> None:
        self.repo.save_document_with_passages("DOC-C", "c.pdf", "hash-c", "input/c.pdf",
                                              [("DOC-C-P0001", None, "Loose text.")])
        report = self.run_index()
        self.assertEqual(report.skipped_no_project, 1)
        self.assertNotIn("Loose text.", self.gemini.texts)

    def test_deleted_passages_are_reported_then_pruned(self) -> None:
        self.run_index()
        self.repo.delete_document(self.doc_b)
        report = self.run_index()
        self.assertEqual(report.orphans, ["DOC-B-P0001", "DOC-B-P0002"])
        self.assertEqual(self.count(), 5)
        report = self.run_index(prune=True)
        self.assertEqual(report.pruned, 2)
        self.assertEqual(self.count(), 3)

    def test_foreign_points_in_the_collection_are_left_alone(self) -> None:
        self.run_index()
        self.qdrant.upsert(COLLECTION, points=[models.PointStruct(
            id=1, vector={"dense": [0.5] * DIM}, payload={"other": "data"})])
        report = self.run_index(prune=True)
        self.assertEqual(report.orphans, [])
        self.assertEqual(self.count(), 6)

    def test_dry_run_writes_nothing(self) -> None:
        report = self.run_index(dry_run=True)
        self.assertEqual((report.embedded, report.reused, report.embedding_calls), (4, 1, 4))
        self.assertEqual(self.gemini.calls, [])
        self.assertFalse(self.qdrant.collection_exists(COLLECTION))

    def test_vector_size_mismatch_is_refused(self) -> None:
        self.qdrant.create_collection(
            COLLECTION,
            vectors_config={"dense": models.VectorParams(size=DIM * 2, distance=models.Distance.COSINE)},
            sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        with self.assertRaisesRegex(self.indexer.IndexerError, "GEMINI_EMBEDDING_DIM"):
            self.run_index()

    def test_old_single_vector_layout_is_refused(self) -> None:
        self.qdrant.create_collection(COLLECTION, vectors_config=models.VectorParams(
            size=DIM, distance=models.Distance.COSINE))
        with self.assertRaisesRegex(self.indexer.IndexerError, "hybrid layout"):
            self.run_index()

    def test_sparse_version_change_rewrites_points_without_gemini(self) -> None:
        self.run_index()
        self.gemini.calls.clear()
        with mock.patch.object(self.sparse, "VERSION", "bm25-test"):
            report = self.run_index()
            self.assertEqual(self.gemini.calls, [])
            self.assertEqual((report.embedded, report.reused), (0, 5))
            self.assertEqual(self.payload("DOC-A-P0001")["sparse_version"], "bm25-test")

    def search(self, project_id: str, query: str, mode: str = "hybrid", top_k: int = 10) -> list[str]:
        return self.retriever.search_passage_ids(project_id, query, top_k, mode=mode, client=self.qdrant,
                                                 embed_client=self.gemini, collection=COLLECTION)

    def test_every_mode_is_filtered_by_project(self) -> None:
        self.run_index()
        for mode in ("hybrid", "dense", "sparse"):
            with self.subTest(mode=mode):
                hits = self.search(self.p1, "Audit logs passwords reports", mode)
                self.assertTrue(hits)
                self.assertTrue(set(hits) <= {"DOC-A-P0001", "DOC-A-P0002", "DOC-A-P0003"}, hits)

    def test_sparse_finds_exact_terms(self) -> None:
        self.run_index()
        self.assertEqual(self.search(self.p1, "CSV", "sparse"), ["DOC-A-P0003"])
        self.assertEqual(self.search(self.p1, "csv export", "hybrid")[0], "DOC-A-P0003")

    def test_hybrid_returns_ranked_passage_ids(self) -> None:
        self.run_index()
        self.assertEqual(self.search(self.p2, "Audit logs are kept for one year.", top_k=1),
                         ["DOC-B-P0002"])

    def test_search_passages_returns_payload_fields(self) -> None:
        self.run_index()
        [hit] = self.retriever.search_passages(self.p1, "log in", 1, client=self.qdrant,
                                               embed_client=self.gemini, collection=COLLECTION)
        self.assertEqual(hit["passage_id"], "DOC-A-P0001")
        self.assertEqual((hit["filename"], hit["page"]), ("a.pdf", 1))


class SparseEncoderTest(unittest.TestCase):
    def test_vietnamese_terms_keep_diacritics(self) -> None:
        from retrieval import sparse
        self.assertEqual(sparse.tokenize("Mã số Người dùng, MA"), ["mã", "số", "người", "dùng", "ma"])
        self.assertNotEqual(sparse.encode_query("mã")[0], sparse.encode_query("ma")[0])

    def test_nfc_and_nfd_text_give_the_same_vector(self) -> None:
        import unicodedata
        from retrieval import sparse
        text = "Người dùng đăng nhập"
        self.assertEqual(sparse.encode_document(text),
                         sparse.encode_document(unicodedata.normalize("NFD", text)))

    def test_repeated_terms_weigh_more_but_saturate(self) -> None:
        from retrieval import sparse
        _, [one] = sparse.encode_document("log")
        _, [three] = sparse.encode_document("log log log")
        self.assertGreater(three, one)
        self.assertLess(three, 3 * one)


if __name__ == "__main__":
    unittest.main()
