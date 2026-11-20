"""Load PDF / DOCX / TXT / MD files, split them into paragraphs with Gemini,
and store them in the `documents` and `source_passages` tables.

Pipeline per file:
  1. Hash the bytes. If the document is already stored with passages, skip it
     (no model call).
  2. Send it to Gemini:
       PDF       -> the file itself (inline bytes, application/pdf)
       DOCX      -> text extracted locally (the API does not accept .docx)
       TXT / MD  -> the text
     Gemini returns {"paragraphs": [{"page", "text"}]} as JSON.
  3. Verify every paragraph is verbatim source text (whitespace, punctuation
     and case are ignored). PDF paragraphs are checked against the text layer
     of the page Gemini named (needs pdfplumber); others against the full text.
     Paragraphs that fail are rejected and never stored: passages are evidence.
  4. Assign deterministic ids and save document + passages in one transaction:
       document  DOC-<first 12 hex of sha256>
       passage   <document id>-P0001, -P0002, ... in reading order
     and link the document to the project, in the same transaction.

Every ingest is for one project (services.project_registry), which must exist
before any model call. A file already stored (same hash) is not re-read; it is
only linked to the project, so one file can serve several projects.

Usage:
    from services import document_loader
    passages = document_loader.ingest(project_id="PRJ-...")            # every file in src/input
    passages = document_loader.ingest(["a.pdf"], project_id="PRJ-...") # specific files

    uv run python -m services.document_loader --project PRJ-... [files...]  # from src/, prints a report
"""

from __future__ import annotations

import hashlib
import io
import logging
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, ValidationError

import config
from database.evidence_rules import normalize_for_match
from database.sqlite_repository import SQLiteRepository
from llm_clients import gemini_client

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".txt": "text", ".md": "markdown"}
MAX_INLINE_BYTES = 18 * 1024 * 1024  # Gemini inline request limit is ~20 MB

SYSTEM_PROMPT = """\
You split documents into paragraphs for a requirements-traceability system.
Every paragraph you return is stored as evidence, so it must be the document's
own words.

Rules:
- Copy text verbatim. Do not summarize, paraphrase, translate, correct,
  complete, or reorder anything. Keep the original language.
- Return paragraphs in reading order. Each paragraph, heading, list item, and
  table row is its own entry. For a table row, join the cells with " | ".
- Within a paragraph, join lines that were broken only by layout, and rejoin
  words hyphenated across a line break.
- Omit running page headers and footers, page numbers, and empty lines.
- "page": the 1-based page number where the paragraph starts for paged
  documents (PDF); null for plain text.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "text": {"type": "string"},
                },
                "required": ["page", "text"],
            },
        }
    },
    "required": ["paragraphs"],
}


class DocumentLoaderError(Exception):
    """A file could not be ingested. Nothing was stored for that file."""


class ParsedParagraph(BaseModel):
    page: int | None
    text: str


class ParsedDocument(BaseModel):
    paragraphs: list[ParsedParagraph]


@dataclass
class IngestResult:
    filename: str
    document_id: str
    passages: list[dict[str, Any]]
    skipped: bool = False                 # already ingested; no model call
    rejected: list[ParsedParagraph] = field(default_factory=list)  # not verbatim
    unverified: int = 0                   # PDF text with no text layer to check
    coverage: float | None = None         # share of source text kept as passages


# ------------------------------------------------------------------- public


def ingest(
    files: Iterable[str | Path] | None = None,
    *,
    project_id: str,
    repo: SQLiteRepository | None = None,
    client: Any = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Ingest files (default: every supported file in config.INPUT_DIR) for a project.

    Returns the stored passages of all files, in file then reading order.
    """
    results = ingest_with_report(files, project_id=project_id, repo=repo, client=client, model=model)
    return [p for r in results for p in r.passages]


def ingest_with_report(
    files: Iterable[str | Path] | None = None,
    *,
    project_id: str,
    repo: SQLiteRepository | None = None,
    client: Any = None,
    model: str | None = None,
) -> list[IngestResult]:
    paths = [Path(f) for f in files] if files is not None else discover_files(config.INPUT_DIR)
    own_repo = repo is None
    repo = repo or SQLiteRepository(config.DB_PATH)
    try:
        _require_project(repo, project_id)  # before any model call
        results = []
        for path in paths:
            result = ingest_file(path, project_id=project_id, repo=repo, client=client, model=model)
            _log_result(result)
            results.append(result)
        return results
    finally:
        if own_repo:
            repo.close()


def _require_project(repo: SQLiteRepository, project_id: str) -> None:
    if not project_id or repo.get_project(project_id) is None:
        raise DocumentLoaderError(
            f"Project {project_id!r} not found; create one with: "
            "python -m services.project_registry create NAME"
        )


def discover_files(directory: str | Path) -> list[Path]:
    """Supported files directly inside `directory`, sorted by name."""
    directory = Path(directory)
    if not directory.is_dir():
        raise DocumentLoaderError(f"Input directory not found: {directory}")
    files = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
        else:
            logger.warning("Skipping unsupported file: %s", path.name)
    return files


def ingest_file(
    path: str | Path,
    *,
    project_id: str,
    repo: SQLiteRepository,
    client: Any = None,
    model: str | None = None,
) -> IngestResult:
    _require_project(repo, project_id)
    path = Path(path).resolve()
    kind = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if kind is None:
        raise DocumentLoaderError(
            f"Unsupported file type {path.suffix!r} ({path.name}); "
            f"supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    if not path.is_file():
        raise DocumentLoaderError(f"File not found: {path}")

    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    document_id = f"DOC-{file_hash[:12]}"

    existing = repo.get_document_by_hash(file_hash)
    if existing is not None:
        stored = repo.list_passages(existing["id"])
        if stored:
            repo.link_document(project_id, existing["id"])  # no-op if already linked
            return IngestResult(path.name, existing["id"], stored, skipped=True)
        document_id = existing["id"]  # stored earlier without passages: fill it in

    source = _prepare_source(path, kind, data)
    parsed = _call_gemini(client, model or config.GEMINI_MODEL_LOADER, source.contents, path.name)
    accepted, rejected, unverified = _verify(parsed.paragraphs, source)
    if not accepted:
        raise DocumentLoaderError(
            f"{path.name}: no verbatim paragraphs to store "
            f"({len(rejected)} rejected); nothing was saved"
        )

    passages = [(f"{document_id}-P{i:04d}", page, text) for i, (page, text) in enumerate(accepted, 1)]
    document_id = repo.save_document_with_passages(
        document_id=document_id,
        filename=path.name,
        file_hash=file_hash,
        file_path=_display_path(path),
        passages=passages,
        project_id=project_id,
    )
    return IngestResult(
        filename=path.name,
        document_id=document_id,
        passages=repo.list_passages(document_id),
        rejected=rejected,
        unverified=unverified,
        coverage=_coverage([t for _, t in accepted], source.full_text),
    )


# ---------------------------------------------------------- source handling


@dataclass
class _Source:
    kind: str
    contents: list[Any]                    # what is sent to Gemini
    full_text: str | None                  # for coverage; None if unknown
    pages: list[str] | None = None         # normalized text per PDF page
    page_count: int | None = None


def _prepare_source(path: Path, kind: str, data: bytes) -> _Source:
    if kind == "pdf":
        if len(data) > MAX_INLINE_BYTES:
            raise DocumentLoaderError(
                f"{path.name} is {len(data) / 1e6:.1f} MB; inline PDFs must be under "
                f"{MAX_INLINE_BYTES / 1e6:.0f} MB"
            )
        page_texts = _pdf_page_texts(data)
        return _Source(
            kind=kind,
            contents=[
                {"inline_data": {"mime_type": "application/pdf", "data": data}},
                "Split this PDF document into paragraphs.",
            ],
            full_text="\n".join(page_texts) if page_texts is not None else None,
            pages=[_normalize(t) for t in page_texts] if page_texts is not None else None,
            page_count=len(page_texts) if page_texts is not None else None,
        )

    text = _docx_text(data) if kind == "docx" else _decode_text(data, path.name)
    if not text.strip():
        raise DocumentLoaderError(f"{path.name} contains no text")
    return _Source(
        kind=kind,
        contents=[
            f"Split this {kind} document into paragraphs. Use null for every page.\n\n"
            f"<document>\n{text}\n</document>"
        ],
        full_text=text,
    )


def _pdf_page_texts(data: bytes) -> list[str] | None:
    """Text layer of each PDF page, or None if pdfplumber is not installed."""
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber is not installed; PDF passages cannot be verified")
        return None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    """Paragraph text of word/document.xml, one paragraph per block."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            root = ElementTree.fromstring(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise DocumentLoaderError(f"Not a valid .docx file: {e}") from e
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts)
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _decode_text(data: bytes, name: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise DocumentLoaderError(f"{name} is not UTF-8 text: {e}") from e


# ------------------------------------------------------------------- Gemini


def _call_gemini(client: Any, model: str, contents: list[Any], name: str) -> ParsedDocument:
    try:
        text = gemini_client.generate_json(
            client,
            name=name,
            model=model,
            contents=contents,
            system_instruction=SYSTEM_PROMPT,
            response_schema=RESPONSE_SCHEMA,
        )
    except gemini_client.GeminiError as e:
        raise DocumentLoaderError(f"{e}; nothing was saved") from e
    try:
        return ParsedDocument.model_validate_json(text)
    except ValidationError as e:
        raise DocumentLoaderError(f"{name}: Gemini response is not valid JSON output: {e}") from e


# ------------------------------------------------------------ verification


# Same normalization the evidence check uses (database.evidence_rules).
_normalize = normalize_for_match


def _clean(text: str) -> str:
    return " ".join(text.split())


def _verify(
    paragraphs: list[ParsedParagraph], source: _Source
) -> tuple[list[tuple[int | None, str]], list[ParsedParagraph], int]:
    """Split model output into (accepted (page, text), rejected, unverified count)."""
    accepted: list[tuple[int | None, str]] = []
    rejected: list[ParsedParagraph] = []
    unverified = 0
    full = _normalize(source.full_text) if source.kind != "pdf" and source.full_text else ""

    for para in paragraphs:
        text = _clean(para.text)
        norm = _normalize(text)
        if not norm:
            continue  # separators, stray bullets: nothing to keep

        if source.kind != "pdf":
            if norm in full:
                accepted.append((None, text))
            else:
                rejected.append(para)
            continue

        page = para.page
        if page is None or page < 1 or (source.page_count is not None and page > source.page_count):
            rejected.append(para)
            continue
        if source.pages is None or not any(source.pages[max(page - 2, 0):page + 1]):
            unverified += 1  # no text layer to compare with (scanned page / no pdfplumber)
            accepted.append((page, text))
        elif _in_pdf_pages(norm, page, source.pages):
            accepted.append((page, text))
        else:
            rejected.append(para)
    return accepted, rejected, unverified


def _in_pdf_pages(norm: str, page: int, pages: list[str]) -> bool:
    """True if `norm` is on the named page or a neighbour (the model may be off
    by one), or starts on one page and continues on the next."""
    i = page - 1
    for j in (i, i - 1, i + 1):
        if 0 <= j < len(pages) and norm in pages[j]:
            return True
    for j in (i, i - 1):
        if 0 <= j < len(pages) - 1 and _split_across(norm, pages[j], pages[j + 1]):
            return True
    return False


def _split_across(norm: str, first: str, second: str) -> bool:
    """norm = A + B with A on `first` and B on `second` (headers/footers between)."""
    lo, hi = 0, len(norm)  # longest prefix of norm found in `first`
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if norm[:mid] in first:
            lo = mid
        else:
            hi = mid - 1
    return 0 < lo < len(norm) and norm[lo:] in second


def _coverage(texts: list[str], full_text: str | None) -> float | None:
    if not full_text:
        return None
    total = len(_normalize(full_text))
    return round(min(sum(len(_normalize(t)) for t in texts) / total, 1.0), 3) if total else None


# ------------------------------------------------------------------ helpers


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(config.SRC_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def _log_result(r: IngestResult) -> None:
    if r.skipped:
        logger.info("%s: already ingested as %s (%d passages)", r.filename, r.document_id, len(r.passages))
        return
    logger.info(
        "%s: %s, %d passages, %d rejected, %d unverified, coverage %s",
        r.filename, r.document_id, len(r.passages), len(r.rejected), r.unverified,
        f"{r.coverage:.0%}" if r.coverage is not None else "n/a",
    )
    for para in r.rejected:
        logger.warning("  rejected (page %s): %.100s", para.page, para.text)


if __name__ == "__main__":
    # Windows consoles default to cp1252 and cannot print Vietnamese text.
    from app_logging import setup_cli_logging

    setup_cli_logging("document_loader")
    import argparse

    parser = argparse.ArgumentParser(prog="python -m services.document_loader",
                                     description="Ingest source documents for a project.")
    parser.add_argument("--project", required=True, help="project id (services.project_registry)")
    parser.add_argument("files", nargs="*", help=f"default: every supported file in {config.INPUT_DIR}")
    args = parser.parse_args()
    try:
        ingest_with_report(args.files or None, project_id=args.project)
    except DocumentLoaderError as e:
        logger.error("%s", e)
        sys.exit(1)
