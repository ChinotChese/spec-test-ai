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

    uv run python -m agents.requirement_extractor --project PRJ-... [DOC-id ...]
        # from src/: reads the project's documents from the DB, prints drafts

extract() itself is project-agnostic: it only sees the passages it is given.
The project is attached when the drafts are saved
(requirement_registry.save_candidates(drafts, project_id=...)).
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


def _api_error_types() -> tuple[type[BaseException], ...]:
    """google.genai.errors.APIError if the SDK is installed (it may not be in tests)."""
    try:
        from google.genai import errors
    except ImportError:
        return ()
    return (errors.APIError,)


_API_ERRORS = _api_error_types()


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
    error: str | None = None  # set when the document failed and was skipped

    @property
    def failed(self) -> bool:
        return self.error is not None

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
    """Valid requirement drafts for all passages (document by document).

    Documents that fail are skipped (logged as errors); see extract_with_report.
    """
    reports = extract_with_report(passages, client=client, model=model)
    return [r for report in reports for r in report.requirements]


def extract_with_report(
    passages: Iterable[dict[str, Any]], *, client: Any = None, model: str | None = None
) -> list[ExtractionReport]:
    """One report per document, in input order.

    A document that fails (model error, HTTP error, unusable output) is skipped:
    its report has `error` set and no requirements, and the others continue.
    Raises ExtractionError only when no client can be created, or when every
    document failed, so an exhausted quota never looks like "no requirements".
    """
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
        try:
            report = extract_document(document_id, doc_passages, client=client, model=model)
        except ExtractionError as e:
            report = ExtractionReport(document_id, len(doc_passages), error=str(e))
        _log_report(report)
        reports.append(report)
    if reports and all(r.failed for r in reports):
        raise ExtractionError(
            f"All {len(reports)} document(s) failed; first error: {reports[0].error}"
        )
    return reports


def extract_document(
    document_id: str,
    passages: list[dict[str, Any]],
    *,
    client: Any = None,
    model: str | None = None,
) -> ExtractionReport:
    """One model call for the passages of one document.

    Every failure is raised as ExtractionError, including HTTP errors the SDK
    does not retry (400 bad request, 401/403 key or permission, 404 model).
    """
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
    except _API_ERRORS as e:
        status = f" {e.status}" if e.status else ""
        raise ExtractionError(f"{document_id}: Gemini HTTP {e.code}{status}: {e.message}") from e

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
    if r.failed:
        logger.error("%s: skipped, %s", r.document_id, r.error)
        return
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
    from app_logging import setup_cli_logging

    setup_cli_logging("requirement_extractor")

    import argparse

    from database.sqlite_repository import SQLiteRepository

    parser = argparse.ArgumentParser(prog="python -m agents.requirement_extractor",
                                     description="Extract requirement drafts from a project's documents.")
    parser.add_argument("--project", required=True, help="project id (services.project_registry)")
    parser.add_argument("documents", nargs="*", help="only these documents of the project (default: all)")
    args = parser.parse_args()

    with SQLiteRepository(config.DB_PATH) as repo:
        project = repo.get_project(args.project)
        if project is None:
            logger.error("Project %r not found (python -m services.project_registry list)", args.project)
            sys.exit(1)
        outside = [d for d in args.documents if d not in project["document_ids"]]
        if outside:
            logger.error("Not documents of %s: %s", args.project, ", ".join(outside))
            sys.exit(1)
        doc_ids = args.documents or project["document_ids"]
        all_passages = [p for doc_id in doc_ids for p in repo.list_passages(doc_id)]
    try:
        reports = extract_with_report(all_passages)
    except ExtractionError as e:
        logger.error("%s", e)
        sys.exit(1)
    _print_drafts([r for r in reports if not r.failed])
    failed = [r.document_id for r in reports if r.failed]
    if failed:
        logger.error("%d of %d document(s) skipped: %s", len(failed), len(reports), ", ".join(failed))
        sys.exit(1)  # partial result printed above; non-zero so scripts notice
