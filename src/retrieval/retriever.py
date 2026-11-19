"""Search the passage index (built by retrieval.indexer), always within one project.

    search_passage_ids("PRJ-...", "password expiry")      -> ["DOC-...-P0002", ...]
    search_passages("PRJ-...", "password expiry")         -> [{"passage_id", "score", "text", ...}]

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

from typing import Any, Literal

from retrieval import embedding, qdrant_store, sparse

Mode = Literal["hybrid", "dense", "sparse"]

PREFETCH_LIMIT = 50  # candidates per retriever before fusion

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = qdrant_store.make_client()
    return _client


def _models() -> Any:
    from qdrant_client import models
    return models


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
    models = _models()
    project_filter = qdrant_store.kind_filter(qdrant_store.project_condition(project_id))
    common = {
        "collection_name": collection or qdrant_store.collection_name(),
        "limit": top_k,
        "with_payload": with_payload,
    }
    if mode not in ("hybrid", "dense", "sparse"):
        raise ValueError(f"mode must be hybrid, dense or sparse, not {mode!r}")

    dense_query = embedding.embed_query(query, client=embed_client) if mode != "sparse" else None
    indices, values = sparse.encode_query(query)
    sparse_query = models.SparseVector(indices=indices, values=values)
    if mode == "sparse" and not indices:
        return []

    client = client or _get_client()
    if mode == "dense":
        result = client.query_points(query=dense_query, using=qdrant_store.DENSE,
                                     query_filter=project_filter, **common)
    elif mode == "sparse":
        result = client.query_points(query=sparse_query, using=qdrant_store.SPARSE,
                                     query_filter=project_filter, **common)
    else:
        limit = max(PREFETCH_LIMIT, top_k)
        prefetch = [models.Prefetch(query=dense_query, using=qdrant_store.DENSE,
                                    filter=project_filter, limit=limit)]
        if indices:  # a query of punctuation only has no terms
            prefetch.append(models.Prefetch(query=sparse_query, using=qdrant_store.SPARSE,
                                            filter=project_filter, limit=limit))
        result = client.query_points(prefetch=prefetch,
                                     query=models.FusionQuery(fusion=models.Fusion.RRF),
                                     query_filter=project_filter, **common)
    return result.points


def search_passage_ids(project_id: str, query: str, top_k: int = 8, **kwargs: Any) -> list[str]:
    """Passage ids (source_passages.id), best first."""
    points = search_points(project_id, query, top_k, with_payload=["passage_id"], **kwargs)
    return [p.payload["passage_id"] for p in points]


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
