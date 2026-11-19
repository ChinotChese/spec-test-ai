"""Gemini embeddings for the vector index.

Passages are embedded with task_type RETRIEVAL_DOCUMENT, search queries with
RETRIEVAL_QUERY (the model is trained for that asymmetric pair). Model and
size come from config: GEMINI_MODEL_EMBEDDING, GEMINI_EMBEDDING_DIM.

Vectors below 3072 dims are not unit length; the Qdrant collection uses
cosine distance, which normalises them, so no normalisation is done here.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

import config
from llm_clients import gemini_client

logger = logging.getLogger(__name__)

DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"


class EmbeddingError(Exception):
    """Embedding failed or returned unusable vectors."""


def model_name() -> str:
    return config.GEMINI_MODEL_EMBEDDING


def dimension() -> int:
    return config.GEMINI_EMBEDDING_DIM


def content_hash(text: str) -> str:
    """Identity of an embedding: same text + model + size gives the same vector.

    Stored in the point payload; the indexer re-embeds a passage only when
    this hash changes (text edited, or model / size changed in config).
    """
    key = f"{model_name()}\x00{dimension()}\x00{text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def embed_documents(texts: Sequence[str], *, client: Any = None) -> list[list[float]]:
    """Embed passage texts, in batches of GEMINI_EMBEDDING_BATCH_SIZE."""
    return _embed(list(texts), DOCUMENT_TASK, client)


def embed_query(text: str, *, client: Any = None) -> list[float]:
    return _embed([text], QUERY_TASK, client)[0]


def _embed(texts: list[str], task_type: str, client: Any) -> list[list[float]]:
    if not texts:
        return []
    try:
        client = client or gemini_client.make_client()
        batch_size = max(1, min(config.GEMINI_EMBEDDING_BATCH_SIZE, 100))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            vectors.extend(gemini_client.embed_texts(
                client,
                name=f"embed {task_type} [{start + 1}-{start + len(batch)}/{len(texts)}]",
                model=model_name(),
                texts=batch,
                task_type=task_type,
                output_dimensionality=dimension(),
            ))
    except gemini_client.GeminiError as e:
        raise EmbeddingError(str(e)) from e
    for v in vectors:
        if len(v) != dimension():
            raise EmbeddingError(
                f"{model_name()} returned {len(v)}-dim vectors, expected GEMINI_EMBEDDING_DIM={dimension()}"
            )
    return vectors
