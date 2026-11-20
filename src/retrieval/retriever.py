"""Search the passage index (built by retrieval.indexer), always within one project.

    search_passage_ids("PRJ-...", "password expiry")      -> ["DOC-...-P0002", ...]
    search_passages("PRJ-...", "password expiry")         -> [{"passage_id", "score", "text", ...}]
    search_many("PRJ-...", {"scope": "...", "users": "..."}) -> {"scope": [ids], "users": [ids]}

mode:
  "hybrid" (default)  dense (Gemini, meaning) + bm25 (exact terms), fused with
                      Reciprocal Rank Fusion. Score is the RRF score, only
                      meaningful for ordering.
  "dense"             Gemini embedding only, score = cosine similarity.
  "sparse"            BM25 only, score = BM25.

The project filter is applied inside each retriever (prefetch), so the fused
list can only contain passages of that project.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any, Literal

from retrieval import embedding, qdrant_store, sparse

Mode = Literal["hybrid", "dense", "sparse"]
MODES = ("hybrid", "dense", "sparse")

PREFETCH_LIMIT = 50  # candidates per retriever before fusion

logger = logging.getLogger(__name__)

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = qdrant_store.make_client()
    return _client


def _models() -> Any:
    from qdrant_client import models
    return models


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")


def _request(
    project_id: str,
    query: str,
    dense_vector: list[float] | None,
    top_k: int,
    mode: str,
    with_payload: bool | list[str],
) -> Any | None:
    """One QueryRequest, or None when the query cannot match anything."""
    models = _models()
    project_filter = qdrant_store.kind_filter(qdrant_store.project_condition(project_id))
    indices, values = sparse.encode_query(query)
    sparse_vector = models.SparseVector(indices=indices, values=values)
    common = {"filter": project_filter, "limit": top_k, "with_payload": with_payload}
    if mode == "dense":
        return models.QueryRequest(query=dense_vector, using=qdrant_store.DENSE, **common)
    if mode == "sparse":
        if not indices:
            return None
        return models.QueryRequest(query=sparse_vector, using=qdrant_store.SPARSE, **common)
    limit = max(PREFETCH_LIMIT, top_k)
    prefetch = [models.Prefetch(query=dense_vector, using=qdrant_store.DENSE,
                                filter=project_filter, limit=limit)]
    if indices:  # a query of punctuation only has no terms
        prefetch.append(models.Prefetch(query=sparse_vector, using=qdrant_store.SPARSE,
                                        filter=project_filter, limit=limit))
    return models.QueryRequest(prefetch=prefetch, query=models.FusionQuery(fusion=models.Fusion.RRF),
                               **common)


def _run(
    project_id: str,
    queries: list[str],
    top_k: int,
    mode: str,
    with_payload: bool | list[str],
    client: Any,
    embed_client: Any,
    collection: str | None,
) -> list[list[Any]]:
    """ScoredPoints per query: one embedding call and one Qdrant call in total."""
    _check_mode(mode)
    if not queries:
        return []
    collection = collection or qdrant_store.collection_name()
    t0 = time.perf_counter()
    vectors: list[list[float] | None] = (
        embedding.embed_queries(queries, client=embed_client) if mode != "sparse" else [None] * len(queries)
    )
    t1 = time.perf_counter()
    requests = [_request(project_id, q, v, top_k, mode, with_payload) for q, v in zip(queries, vectors)]
    sendable = [r for r in requests if r is not None]
    responses = iter((client or _get_client()).query_batch_points(
        collection_name=collection, requests=sendable,
    ) if sendable else [])
    results = [next(responses).points if r is not None else [] for r in requests]
    t2 = time.perf_counter()
    hits = sum(len(r) for r in results)
    unique = len({p.id for r in results for p in r})
    logger.info("retrieval %s: collection=%s project=%s queries=%d top_k=%d hits=%d unique=%d "
                "empty=%d embed=%.0fms qdrant=%.0fms", mode, collection, project_id, len(queries), top_k,
                hits, unique, sum(1 for r in results if not r), (t1 - t0) * 1000, (t2 - t1) * 1000)
    return results


def search_points(
    project_id: str,
    query: str,
    top_k: int = 8,
    *,
    mode: Mode = "hybrid",
    with_payload: bool | list[str] = True,
    client: Any = None,
    embed_client: Any = None,
    collection: str | None = None,
) -> list[Any]:
    """Qdrant ScoredPoints of `project_id` best matching `query`."""
    return _run(project_id, [query], top_k, mode, with_payload, client, embed_client, collection)[0]


def search_passage_ids(project_id: str, query: str, top_k: int = 8, **kwargs: Any) -> list[str]:
    """Passage ids (source_passages.id), best first."""
    points = search_points(project_id, query, top_k, with_payload=["passage_id"], **kwargs)
    return [p.payload["passage_id"] for p in points]


def search_many(
    project_id: str,
    queries: Mapping[str, str],
    top_k: int = 8,
    *,
    mode: Mode = "hybrid",
    client: Any = None,
    embed_client: Any = None,
    collection: str | None = None,
) -> dict[str, list[str]]:
    """Passage ids per named query, best first: {key: [passage_id, ...]}.

    All queries are embedded in one Gemini call (up to 100) and searched in
    one Qdrant batch request.
    """
    keys = list(queries)
    results = _run(project_id, [queries[k] for k in keys], top_k, mode, ["passage_id"],
                   client, embed_client, collection)
    return {k: [p.payload["passage_id"] for p in points] for k, points in zip(keys, results)}


def search_passages(project_id: str, query: str, top_k: int = 8, **kwargs: Any) -> list[dict[str, Any]]:
    """Passages as dicts: passage_id, document_id, filename, page, text, score."""
    return [
        {
            "passage_id": p.payload["passage_id"],
            "document_id": p.payload.get("document_id"),
            "filename": p.payload.get("filename"),
            "page": p.payload.get("page"),
            "text": p.payload.get("text"),
            "score": p.score,
        }
        for p in search_points(project_id, query, top_k, **kwargs)
    ]


def retrieve_evidence(
    project_id: str,
    query_vector: list[float],
    top_k: int = 8,
    *,
    client: Any = None,
    collection: str | None = None,
) -> list[Any]:
    """Dense-only search with a precomputed query vector (ScoredPoint list)."""
    result = (client or _get_client()).query_points(
        collection_name=collection or qdrant_store.collection_name(),
        query=query_vector,
        using=qdrant_store.DENSE,
        query_filter=qdrant_store.kind_filter(qdrant_store.project_condition(project_id)),
        limit=top_k,
        with_payload=True,
    )
    return result.points
