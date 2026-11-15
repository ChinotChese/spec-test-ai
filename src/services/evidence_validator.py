"""Check that each requirement's evidence really comes from the source.

Valid evidence (one definition, used before saving AND again at approval):
  - the requirement cites at least one passage, and EVERY evidence item:
      * points to a passage that exists in source_passages,
      * carries a quote (a missing or blank quote cannot be verified),
      * whose text is found inside that same passage.
  Quotes are compared after the loader's normalization (letters and digits
  only, case-folded), so whitespace, punctuation, and hyphenation differences
  do not matter, but changed or invented words do.

A requirement with ANY invalid evidence item is invalid as a whole: it is
never trimmed to its valid items, because the statement would then claim
more than its remaining evidence shows.

Usage:
    from services import evidence_validator
    valid = evidence_validator.validate(candidates)          # app.py step 3
    report = evidence_validator.validate_with_report(candidates)
    problems = evidence_validator.evidence_problems(requirement, repo)  # [] = valid
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

import config
from database import evidence_rules
from database.sqlite_repository import SQLiteRepository
from schemas.requirement import RequirementDraft

logger = logging.getLogger(__name__)


@dataclass
class InvalidCandidate:
    candidate: RequirementDraft
    problems: list[str]


@dataclass
class ValidationReport:
    valid: list[RequirementDraft] = field(default_factory=list)
    invalid: list[InvalidCandidate] = field(default_factory=list)


# ------------------------------------------------------------------- public


def validate(
    candidates: Iterable[RequirementDraft | Mapping[str, Any]], *, repo: SQLiteRepository | None = None
) -> list[RequirementDraft]:
    """Candidates whose evidence is valid, in input order. Invalid ones are logged."""
    return validate_with_report(candidates, repo=repo).valid


def validate_with_report(
    candidates: Iterable[RequirementDraft | Mapping[str, Any]], *, repo: SQLiteRepository | None = None
) -> ValidationReport:
    drafts = [as_draft(c) for c in candidates]
    with using_repo(repo) as r:
        passages = _passage_texts(r, [e.passage_id for d in drafts for e in d.evidence])
    report = ValidationReport()
    for draft in drafts:
        problems = _problems(draft.evidence, passages)
        if problems:
            report.invalid.append(InvalidCandidate(draft, problems))
            logger.warning("invalid evidence: %.80s -> %s", draft.statement, "; ".join(problems))
        else:
            report.valid.append(draft)
    logger.info("evidence check: %d valid, %d invalid", len(report.valid), len(report.invalid))
    return report


def evidence_problems(
    requirement: RequirementDraft | Mapping[str, Any], repo: SQLiteRepository
) -> list[str]:
    """Problems with one requirement's evidence; an empty list means valid.

    Accepts a draft, a Requirement, or a repository row (dict with "evidence").
    """
    evidence = _evidence_of(requirement)
    passages = _passage_texts(repo, [e["passage_id"] for e in evidence])
    return _problems(evidence, passages)


# ------------------------------------------------------------------ checks


def _problems(evidence: list[Any], passages: dict[str, str]) -> list[str]:
    # The rule itself lives in database.evidence_rules, so the repository's
    # approval check and this pre-save check can never disagree.
    return evidence_rules.evidence_problems([_as_dict(e) for e in evidence], passages)


# ------------------------------------------------------------------ helpers


class using_repo:
    """Use the given repository, or open (and close) the configured database."""

    def __init__(self, repo: SQLiteRepository | None):
        self._given, self._own = repo, None

    def __enter__(self) -> SQLiteRepository:
        if self._given is not None:
            return self._given
        self._own = SQLiteRepository(config.DB_PATH)
        return self._own

    def __exit__(self, *exc: object) -> None:
        if self._own is not None:
            self._own.close()


def _passage_texts(repo: SQLiteRepository, passage_ids: list[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(pid for pid in passage_ids if pid))
    return {p["id"]: p["text"] for p in repo.get_passages(ids)}


def as_draft(candidate: RequirementDraft | Mapping[str, Any]) -> RequirementDraft:
    if isinstance(candidate, RequirementDraft):
        return candidate
    fields = RequirementDraft.model_fields
    return RequirementDraft.model_validate({k: v for k, v in dict(candidate).items() if k in fields})


def _evidence_of(requirement: RequirementDraft | Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence = requirement.evidence if isinstance(requirement, BaseModel) else requirement.get("evidence")
    return [_as_dict(e) for e in evidence or []]


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, BaseModel):
        return item.model_dump()
    if isinstance(item, str):
        return {"passage_id": item, "quote": None}
    return dict(item)
