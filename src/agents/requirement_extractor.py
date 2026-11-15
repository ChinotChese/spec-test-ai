"""Extract functional and nonfunctional requirements from source passages.

    passages (from services.document_loader / repo.list_passages)
      -> one Gemini call per document (structured output)
      -> list[RequirementDraft]   (schemas/requirement.py)

Every returned requirement:
  - validates as RequirementDraft (FR/NFR field rules, >= 1 evidence item),
  - cites only passage ids that were sent in the same call (same document).
Items that fail are rejected, not repaired, and reported with the reason.

Not done here (later pipeline steps):
  - checking that each quote really appears in its passage -> evidence_validator
  - saving to the requirements table                       -> requirement_registry

Usage:
    from agents import requirement_extractor
    drafts = requirement_extractor.extract(passages)

    uv run python -m agents.requirement_extractor [DOC-id ...]   # from src/, reads the DB, prints drafts
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

import config
from prompts.requirement_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas.requirement import Evidence, RequirementDraft
from schemas.srs import Priority, VerificationMethod
from llm_clients import gemini_client

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """A document could not be processed (model call failed or output unusable)."""


# Generation schema sent to Gemini. RequirementDraft keeps every type-specific
# field optional and checks the FR/NFR rules in a validator, which a JSON schema
# cannot express. Two explicit variants make the model emit the right required
# fields per type; each item is still validated as RequirementDraft afterwards.
def _desc(name: str) -> str | None:
    return RequirementDraft.model_fields[name].description


class _FunctionalOut(BaseModel):
    type: Literal["functional"]
    statement: str = Field(description=_desc("statement"))
    priority: Priority = Field(description=_desc("priority"))
    evidence: list[Evidence] = Field(min_length=1, description=_desc("evidence"))
    feature: str = Field(description=_desc("feature"))
    stimulus_or_condition: str | None = Field(default=None, description=_desc("stimulus_or_condition"))
    response: str | None = Field(default=None, description=_desc("response"))


class _NonfunctionalOut(BaseModel):
    type: Literal["nonfunctional"]
    statement: str = Field(description=_desc("statement"))
    priority: Priority = Field(description=_desc("priority"))
    evidence: list[Evidence] = Field(min_length=1, description=_desc("evidence"))
    nfr_category: str = Field(description=_desc("nfr_category"))
    verification_method: VerificationMethod = Field(description=_desc("verification_method"))
    acceptance_measure: str = Field(description=_desc("acceptance_measure"))


class RequirementExtraction(BaseModel):
    """Shape of the model's answer; sent to Gemini as response_json_schema."""

    requirements: list[_FunctionalOut | _NonfunctionalOut]


RESPONSE_SCHEMA: dict[str, Any] = RequirementExtraction.model_json_schema()


@dataclass
class Rejected:
    item: Any        # the raw item as returned by the model
    reason: str


@dataclass
class ExtractionReport:
    document_id: str
    passage_count: int
    requirements: list[RequirementDraft] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)

    @property
    def functional(self) -> list[RequirementDraft]:
        return [r for r in self.requirements if r.type == "functional"]

    @property
    def nonfunctional(self) -> list[RequirementDraft]:
        return [r for r in self.requirements if r.type == "nonfunctional"]


# ------------------------------------------------------------------- public


def extract(
    passages: Iterable[dict[str, Any]], *, client: Any = None, model: str | None = None
) -> list[RequirementDraft]:
    """Valid requirement drafts for all passages (document by document)."""
    reports = extract_with_report(passages, client=client, model=model)
    return [r for report in reports for r in report.requirements]


def extract_with_report(
    passages: Iterable[dict[str, Any]], *, client: Any = None, model: str | None = None
) -> list[ExtractionReport]:
    by_document: dict[str, list[dict[str, Any]]] = {}
    for p in passages:
        by_document.setdefault(p["document_id"], []).append(p)
    if by_document and client is None:  # one client for all documents
        try:
            client = gemini_client.make_client()
        except gemini_client.GeminiError as e:
            raise ExtractionError(str(e)) from e
    reports = []
    for document_id, doc_passages in by_document.items():
        report = extract_document(document_id, doc_passages, client=client, model=model)
        _log_report(report)
        reports.append(report)
    return reports


def extract_document(
    document_id: str,
    passages: list[dict[str, Any]],
    *,
    client: Any = None,
    model: str | None = None,
) -> ExtractionReport:
    """One model call for the passages of one document."""
    try:
        text = gemini_client.generate_json(
            client,
            name=document_id,
            model=model or config.GEMINI_MODEL_EXTRACTOR,
            contents=[build_user_prompt(document_id, passages)],
            system_instruction=SYSTEM_PROMPT,
            response_schema=RESPONSE_SCHEMA,
        )
    except gemini_client.GeminiError as e:
        raise ExtractionError(str(e)) from e

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"{document_id}: Gemini response is not JSON: {e}") from e
    items = payload.get("requirements") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ExtractionError(f'{document_id}: Gemini response has no "requirements" list')

    known_ids = {p["id"] for p in passages}
    report = ExtractionReport(document_id, len(passages))
    seen: set[str] = set()
    for item in items:
        draft, reason = _check_item(item, known_ids, seen)
        if draft is None:
            report.rejected.append(Rejected(item, reason))
        else:
            report.requirements.append(draft)
    return report


# ------------------------------------------------------------------ checks


def _check_item(
    item: Any, known_ids: set[str], seen: set[str]
) -> tuple[RequirementDraft | None, str]:
    """Validate one model item; one bad item never discards the others."""
    try:
        draft = RequirementDraft.model_validate(item)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first["loc"]) or "item"
        return None, f"invalid: {where}: {first['msg']}"

    unknown = [pid for pid in draft.passage_ids if pid not in known_ids]
    if unknown:
        return None, f"cites passage ids not in this document: {unknown}"

    key = " ".join(draft.statement.casefold().split())
    if key in seen:
        return None, "duplicate statement"
    seen.add(key)
    return draft, ""


# ------------------------------------------------------------------ helpers


def _log_report(r: ExtractionReport) -> None:
    logger.info(
        "%s: %d passages -> %d requirements (%d FR, %d NFR), %d rejected",
        r.document_id, r.passage_count, len(r.requirements),
        len(r.functional), len(r.nonfunctional), len(r.rejected),
    )
    for rej in r.rejected:
        statement = rej.item.get("statement") if isinstance(rej.item, dict) else rej.item
        logger.warning("  rejected (%s): %.100s", rej.reason, statement)


def _print_drafts(reports: list[ExtractionReport]) -> None:
    for report in reports:
        print(f"\n=== {report.document_id}")
        for d in report.requirements:
            where = d.feature if d.type == "functional" else d.nfr_category
            print(f"[{d.type[:3].upper()} {d.priority:<6} {where}] {d.statement}")
            print(f"    evidence: {', '.join(d.passage_ids)}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from database.sqlite_repository import SQLiteRepository

    with SQLiteRepository(config.DB_PATH) as repo:
        doc_ids = sys.argv[1:] or [d["id"] for d in repo.list_documents()]
        all_passages = [p for doc_id in doc_ids for p in repo.list_passages(doc_id)]
    try:
        _print_drafts(extract_with_report(all_passages))
    except (ExtractionError, gemini_client.GeminiError) as e:
        logger.error("%s", e)
        sys.exit(1)
