"""Index source passages from SQLite into Qdrant (collection QDRANT_COLLECTION).

    python -m retrieval.indexer                   # every passage of every project
    python -m retrieval.indexer --project PRJ-... # only that project's passages
    python -m retrieval.indexer --dry-run         # report what would change
    python -m retrieval.indexer --prune           # also delete points of deleted passages

Running it again is safe and cheap; nothing is indexed twice:
  1. One point per passage, id = uuid5(passage_id). A re-index overwrites the
     passage's own point, so duplicates cannot appear even after a crash.
  2. The payload keeps content_hash = sha256(model, dim, text). A passage whose
     point already has that hash is not embedded again.
  3. Passages whose text is identical (in this run, or already in the index
     under another passage) are embedded once; the vector is reused.
  4. When only the metadata changed (e.g. the document was linked to another
     project), the payload is updated in place, without an embedding call.
  5. Each point also stores a BM25 sparse vector (retrieval.sparse, computed
     locally, no API call). When sparse.VERSION changes, points are rewritten
     with their stored dense vector.

Passages of documents that belong to no project are skipped: retrieval is
always filtered by project, so they could never be found. They are indexed
on the first run after their document is linked.

Embedded points are written batch by batch, so a run stopped by the Gemini
quota keeps what it finished and the next run continues from there.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import config
from database.sqlite_repository import SQLiteRepository
from retrieval import embedding, qdrant_store, sparse

logger = logging.getLogger(__name__)

# Payload keys compared to decide "metadata changed" (content_hash covers text + model).
_META_KEYS = ("passage_id", "document_id", "filename", "page", "project_ids")
_SCAN_FIELDS = ("passage_id", "content_hash", "sparse_version", *_META_KEYS)


class IndexerError(Exception):
    """Indexing could not run (database, Qdrant or Gemini problem)."""


@dataclass
class IndexReport:
    collection: str
    in_scope: int = 0                 # passages considered in this run
    embedded: int = 0                 # points written with a newly computed dense vector
    reused: int = 0                   # points written with a stored dense vector (same text)
    payload_updated: int = 0          # metadata changed, vector kept
    unchanged: int = 0                # already indexed and up to date
    skipped_no_project: int = 0
    skipped_empty: int = 0
    orphans: list[str] = field(default_factory=list)  # indexed passage ids no longer in SQLite
    pruned: int = 0
    embedding_calls: int = 0          # texts sent to Gemini
    dry_run: bool = False


def build_payload(row: dict[str, Any], text_hash: str) -> dict[str, Any]:
    return {
        "kind": qdrant_store.KIND,
        "passage_id": row["id"],
        "document_id": row["document_id"],
        "filename": row["filename"],
        "page": row["page"],
        "text": row["text"],
        "project_ids": list(row["project_ids"]),
        "content_hash": text_hash,
        "sparse_version": sparse.VERSION,
        "embedding_model": embedding.model_name(),
        "embedding_dim": embedding.dimension(),
    }


def index_passages(
    *,
    project_id: str | None = None,
    document_id: str | None = None,
    prune: bool = False,
    dry_run: bool = False,
    repo: SQLiteRepository | None = None,
    qdrant: Any = None,
    embed_client: Any = None,
    collection: str | None = None,
) -> IndexReport:
    """Bring the Qdrant index up to date with source_passages.

    project_id / document_id limit which passages are embedded. Payloads of
    every already-indexed passage are kept in sync either way (cheap, and it
    keeps the project filter of retrieval correct after a link / unlink).
    """
    collection = collection or qdrant_store.collection_name()
    report = IndexReport(collection=collection, dry_run=dry_run)

    own_repo = repo is None
    repo = repo or SQLiteRepository(config.DB_PATH)
    try:
        rows = repo.list_passages_for_index()
    finally:
        if own_repo:
            repo.close()

    try:
        qdrant = qdrant or qdrant_store.make_client()
        if dry_run and not qdrant.collection_exists(collection):
            indexed: dict[str, dict[str, Any]] = {}
        else:
            if dry_run:
                qdrant_store.check_layout(qdrant, collection, embedding.dimension())
            elif qdrant_store.ensure_collection(qdrant, collection, embedding.dimension()):
                logger.info("created collection %s (%d dims, cosine)", collection, embedding.dimension())
            indexed = {
                r.payload["passage_id"]: {"point_id": str(r.id), **r.payload}
                for r in qdrant_store.iter_indexed(qdrant, collection, fields=_SCAN_FIELDS)
                if r.payload and r.payload.get("passage_id")
            }
    except qdrant_store.QdrantStoreError as e:
        raise IndexerError(str(e)) from e
    except Exception as e:  # network / auth errors from qdrant-client
        raise IndexerError(f"Qdrant at {config.QDRANT_HOST}:{config.QDRANT_PORT}: {e}") from e

    to_embed: list[tuple[dict[str, Any], str]] = []        # (row, content_hash)
    payload_updates: list[tuple[str, dict[str, Any]]] = []
    known_ids: set[str] = set()

    for row in rows:
        known_ids.add(row["id"])
        in_scope = (project_id is None or project_id in row["project_ids"]) and (
            document_id is None or row["document_id"] == document_id)
        report.in_scope += in_scope
        if not (row["text"] or "").strip():
            report.skipped_empty += in_scope
            continue
        text_hash = embedding.content_hash(row["text"])
        existing = indexed.get(row["id"])
        if (existing is not None and existing.get("content_hash") == text_hash
                and existing.get("sparse_version") == sparse.VERSION):
            payload = build_payload(row, text_hash)
            if any(existing.get(k) != payload[k] for k in _META_KEYS):
                payload_updates.append((existing["point_id"], payload))
                report.payload_updated += 1
            elif in_scope:
                report.unchanged += 1
            continue
        if not in_scope:
            continue  # missing or outdated, but outside this run's scope
        if not row["project_ids"]:
            report.skipped_no_project += 1
            continue
        to_embed.append((row, text_hash))

    report.orphans = sorted(pid for pid in indexed if pid not in known_ids)

    if dry_run:  # same counting as _embed_and_upsert, without the calls
        have = {p.get("content_hash") for p in indexed.values()}
        for _, h in to_embed:
            if h in have:
                report.reused += 1
            else:
                report.embedded += 1
                report.embedding_calls += 1
                have.add(h)
        return report

    try:
        qdrant_store.set_payloads(qdrant, collection, payload_updates)
        _embed_and_upsert(qdrant, collection, to_embed, indexed, embed_client, report)
        if prune and report.orphans:
            qdrant_store.delete(qdrant, collection,
                                [indexed[pid]["point_id"] for pid in report.orphans])
            report.pruned = len(report.orphans)
    except embedding.EmbeddingError as e:
        raise IndexerError(f"{e} (finished points are kept; run again to continue)") from e
    except qdrant_store.QdrantStoreError as e:
        raise IndexerError(str(e)) from e
    return report


def _embed_and_upsert(
    qdrant: Any,
    collection: str,
    to_embed: list[tuple[dict[str, Any], str]],
    indexed: dict[str, dict[str, Any]],
    embed_client: Any,
    report: IndexReport,
) -> None:
    if not to_embed:
        return
    # Vectors already stored for an identical text (another passage, same hash).
    hash_to_point = {p["content_hash"]: p["point_id"] for p in indexed.values() if p.get("content_hash")}
    reusable = [hash_to_point[h] for _, h in to_embed if h in hash_to_point]
    stored = qdrant_store.get_vectors(qdrant, collection, sorted(set(reusable))) if reusable else {}
    vectors: dict[str, list[float]] = {
        h: stored[pid] for h, pid in hash_to_point.items() if pid in stored
    }

    batch_size = max(1, min(config.GEMINI_EMBEDDING_BATCH_SIZE, 100))
    for batch in _chunks(to_embed, batch_size):
        new_texts: dict[str, str] = {}
        for row, h in batch:
            if h not in vectors:
                new_texts.setdefault(h, row["text"])
        if new_texts:
            hashes = list(new_texts)
            for h, v in zip(hashes, embedding.embed_documents([new_texts[h] for h in hashes],
                                                              client=embed_client)):
                vectors[h] = v
            report.embedding_calls += len(hashes)
        qdrant_store.upsert(qdrant, collection, [
            (qdrant_store.point_id(row["id"]), vectors[h], sparse.encode_document(row["text"]),
             build_payload(row, h))
            for row, h in batch
        ])
        fresh = set(new_texts)
        for _, h in batch:
            if h in fresh:
                report.embedded += 1
                fresh.discard(h)  # later passages with the same text reuse it
            else:
                report.reused += 1
        logger.info("indexed %d/%d passages", report.embedded + report.reused, len(to_embed))


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _log_report(r: IndexReport) -> None:
    prefix = "[dry run] would embed" if r.dry_run else "embedded"
    logger.info(
        "%s: %d passages in scope; %s %d (%d Gemini inputs), reused %d, payload updated %d, "
        "unchanged %d, skipped %d without project, %d empty",
        r.collection, r.in_scope, prefix, r.embedded, r.embedding_calls, r.reused,
        r.payload_updated, r.unchanged, r.skipped_no_project, r.skipped_empty,
    )
    if r.orphans:
        if r.pruned:
            logger.info("pruned %d points of deleted passages", r.pruned)
        else:
            logger.warning("%d indexed passages no longer exist in SQLite (run with --prune): %s",
                           len(r.orphans), ", ".join(r.orphans[:10]) + (" ..." if len(r.orphans) > 10 else ""))


if __name__ == "__main__":
    from app_logging import setup_cli_logging

    setup_cli_logging("indexer", quiet_http=True)
    import argparse

    parser = argparse.ArgumentParser(prog="python -m retrieval.indexer",
                                     description="Embed source passages into Qdrant.")
    parser.add_argument("--project", help="only embed this project's passages")
    parser.add_argument("--document", help="only embed this document's passages")
    parser.add_argument("--prune", action="store_true",
                        help="delete points whose passage no longer exists in SQLite")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()
    try:
        _log_report(index_passages(project_id=args.project, document_id=args.document,
                                   prune=args.prune, dry_run=args.dry_run))
    except IndexerError as e:
        logger.error("%s", e)
        sys.exit(1)
