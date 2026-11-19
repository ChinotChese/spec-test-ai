"""Qdrant access for the passage index (collection QDRANT_COLLECTION).

One point per source passage:
  id       uuid5 of the passage id -> re-indexing a passage overwrites its own
           point, never adds a second one
  vectors  "dense": Gemini embedding, cosine, size GEMINI_EMBEDDING_DIM
           "bm25":  sparse BM25 term weights (retrieval.sparse), IDF applied
                    by Qdrant (Modifier.IDF)
           -> hybrid search = both, fused with RRF (retrieval.retriever)
  payload  kind="source_passage", passage_id, document_id, filename, page,
           text, project_ids (list), content_hash, sparse_version,
           embedding_model, embedding_dim

kind marks the points this module owns. Scans and deletes are always filtered
on it, so a collection shared with other data is never touched outside it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import config

KIND = "source_passage"
DENSE = "dense"
SPARSE = "bm25"
_POINT_NAMESPACE = uuid.UUID("6f1d3c52-8a0e-4c47-9b3e-5a2f0d6e71c4")  # fixed: changing it orphans every point
_INDEXED_FIELDS = ("kind", "project_ids", "document_id", "passage_id", "content_hash")
_SCROLL_PAGE = 256


class QdrantStoreError(Exception):
    """The collection is missing, incompatible, or Qdrant is unreachable."""


def _models() -> Any:
    try:
        from qdrant_client import models
    except ImportError as e:
        raise QdrantStoreError("The qdrant-client package is not installed (pip install qdrant-client)") from e
    return models


def make_client() -> Any:
    """Client from QDRANT_HOST / QDRANT_PORT / QDRANT_API_KEY (/ QDRANT_HTTPS)."""
    try:
        from qdrant_client import QdrantClient
    except ImportError as e:
        raise QdrantStoreError("The qdrant-client package is not installed (pip install qdrant-client)") from e
    common = {"api_key": config.QDRANT_API_KEY, "timeout": config.QDRANT_TIMEOUT}
    if "://" in config.QDRANT_HOST:
        return QdrantClient(url=config.QDRANT_HOST, **common)
    return QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT,
                        https=config.QDRANT_HTTPS, **common)


def collection_name() -> str:
    return config.QDRANT_COLLECTION


def point_id(passage_id: str) -> str:
    """Deterministic point id: the same passage always maps to the same point."""
    return str(uuid.uuid5(_POINT_NAMESPACE, passage_id))


def kind_filter(*must: Any) -> Any:
    models = _models()
    return models.Filter(must=[
        models.FieldCondition(key="kind", match=models.MatchValue(value=KIND)), *must,
    ])


def project_condition(project_id: str) -> Any:
    """project_ids is a list; MatchValue matches when any element equals it."""
    models = _models()
    return models.FieldCondition(key="project_ids", match=models.MatchValue(value=project_id))


def ensure_collection(client: Any, name: str, dim: int) -> bool:
    """Create the collection if missing; refuse one with another layout.

    Returns True when the collection was created.
    """
    models = _models()
    created = False
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        created = True
    else:
        check_layout(client, name, dim)
    for field in _INDEXED_FIELDS:  # idempotent; speeds up the kind / project filters
        client.create_payload_index(name, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD)
    return created


def check_layout(client: Any, name: str, dim: int) -> None:
    models = _models()
    params = client.get_collection(name).config.params
    vectors = params.vectors if isinstance(params.vectors, dict) else {}
    sparse = params.sparse_vectors or {}
    problems = []
    dense = vectors.get(DENSE)
    if dense is None:
        problems.append(f"no dense vector named {DENSE!r}")
    elif dense.size != dim:
        problems.append(f"{DENSE!r} has {dense.size} dims but GEMINI_EMBEDDING_DIM={dim}")
    elif dense.distance != models.Distance.COSINE:
        problems.append(f"{DENSE!r} uses {dense.distance} distance, not cosine")
    if SPARSE not in sparse:
        problems.append(f"no sparse vector named {SPARSE!r}")
    elif sparse[SPARSE].modifier != models.Modifier.IDF:
        problems.append(f"sparse vector {SPARSE!r} has no IDF modifier")
    if problems:
        raise QdrantStoreError(
            f"Collection {name!r} does not have the hybrid layout ({'; '.join(problems)}). "
            "Drop the collection (it is recreated on the next run) or use another QDRANT_COLLECTION"
        )


def iter_indexed(client: Any, name: str, *, fields: Sequence[str]) -> Iterator[Any]:
    """Every point this module owns, payload limited to `fields`, no vectors."""
    offset = None
    while True:
        records, offset = client.scroll(
            name, scroll_filter=kind_filter(), limit=_SCROLL_PAGE, offset=offset,
            with_payload=list(fields), with_vectors=False,
        )
        yield from records
        if offset is None:
            return


def get_vectors(client: Any, name: str, point_ids: Sequence[str]) -> dict[str, list[float]]:
    """Stored dense vectors by point id (ids that do not exist are left out)."""
    result: dict[str, list[float]] = {}
    ids = list(point_ids)
    for start in range(0, len(ids), _SCROLL_PAGE):
        for r in client.retrieve(name, ids=ids[start:start + _SCROLL_PAGE],
                                 with_payload=False, with_vectors=[DENSE]):
            vector = r.vector.get(DENSE) if isinstance(r.vector, dict) else None
            if isinstance(vector, list):
                result[str(r.id)] = vector
    return result


def upsert(
    client: Any,
    name: str,
    points: Sequence[tuple[str, list[float], tuple[list[int], list[float]], dict[str, Any]]],
) -> None:
    """Write (point_id, dense, (sparse indices, values), payload); an existing id is replaced."""
    if not points:
        return
    models = _models()
    client.upsert(name, wait=True, points=[
        models.PointStruct(id=pid, payload=payload, vector={
            DENSE: dense,
            SPARSE: models.SparseVector(indices=sparse[0], values=sparse[1]),
        })
        for pid, dense, sparse, payload in points
    ])


def set_payloads(client: Any, name: str, updates: Sequence[tuple[str, dict[str, Any]]]) -> None:
    """Overwrite payload keys of existing points, keeping their vectors."""
    if not updates:
        return
    models = _models()
    for start in range(0, len(updates), _SCROLL_PAGE):
        client.batch_update_points(name, wait=True, update_operations=[
            models.SetPayloadOperation(set_payload=models.SetPayload(payload=payload, points=[pid]))
            for pid, payload in updates[start:start + _SCROLL_PAGE]
        ])


def delete(client: Any, name: str, point_ids: Sequence[str]) -> None:
    """Delete points by id, restricted to the ones this module owns."""
    if not point_ids:
        return
    models = _models()
    client.delete(name, wait=True, points_selector=models.FilterSelector(
        filter=kind_filter(models.HasIdCondition(has_id=list(point_ids)))
    ))
