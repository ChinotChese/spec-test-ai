"""Requirement registry: store reviewed-to-be requirements and gate approval.

    save_candidates(drafts)  -> stored as "pending" with ids RQ-0001, RQ-0002, ...
    approve(id) / reject(id) -> human review decisions
    get_approved()           -> what the SRS generator may use

Approval rule: a requirement is approved ONLY by an explicit approve() call,
and approve() refuses unless its stored evidence is valid at that moment
(database.evidence_rules: every cited passage exists and every quote is
found in its passage). Nothing in this module approves automatically:
save_candidates() always stores "pending", whatever the input says.
The repository enforces the same rule on every path to "approved"
(set_requirement_status, add with status "approved", evidence edits on an
approved requirement), so it cannot be bypassed through the repository API.

Ids: "RQ-" + 4 digits, sequential after the highest existing RQ number,
assigned in the same transaction as the inserts. They are storage ids, not the
positional SRS ids (REQ-1.1 / NF-2.1), and do not encode FR/NFR because the
type can be edited.

Command line (from src/):
    python -m services.requirement_registry list [pending|approved|rejected]
    python -m services.requirement_registry show RQ-0001
    python -m services.requirement_registry approve RQ-0001 [RQ-0002 ...]
    python -m services.requirement_registry reject RQ-0003 [...]
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from database.sqlite_repository import RepositoryError, SQLiteRepository
from schemas.requirement import Requirement, RequirementDraft
from services import evidence_validator

logger = logging.getLogger(__name__)

ID_PREFIX = "RQ-"
ID_WIDTH = 4


class RegistryError(Exception):
    """A registry operation was refused; nothing was changed."""


@dataclass
class SaveResult:
    saved_ids: list[str] = field(default_factory=list)
    duplicates: list[tuple[str, str]] = field(default_factory=list)  # (statement, existing id)


# ------------------------------------------------------------------- public


def save_candidates(
    candidates: Iterable[RequirementDraft | Mapping[str, Any]], *, repo: SQLiteRepository | None = None
) -> SaveResult:
    """Store candidates as "pending" with new RQ- ids, all or nothing.

    - Every candidate must have valid evidence (run evidence_validator.validate
      first); otherwise RegistryError is raised and nothing is stored.
    - A candidate whose statement already exists (any status; case and
      whitespace ignored) or repeats within the batch is skipped and reported.
      Reworded duplicates are NOT detected.
    """
    drafts = [_as_draft(c) for c in candidates]
    with _repo(repo) as r:
        invalid = [
            (d.statement, problems)
            for d in drafts
            if (problems := evidence_validator.evidence_problems(d, r))
        ]
        if invalid:
            details = "; ".join(f"{s[:60]!r}: {', '.join(p)}" for s, p in invalid[:3])
            raise RegistryError(
                f"{len(invalid)} candidate(s) have invalid evidence, nothing was saved "
                f"(validate with evidence_validator first): {details}"
            )

        existing = {_statement_key(row["statement"]): row["id"] for row in r.list_requirements()}
        result = SaveResult()
        rows, batch_keys = [], set()
        for d in drafts:
            key = _statement_key(d.statement)
            if key in existing:
                result.duplicates.append((d.statement, existing[key]))
            elif key in batch_keys:
                result.duplicates.append((d.statement, "(same batch)"))
            else:
                batch_keys.add(key)
                rows.append({**d.model_dump(), "status": "pending"})  # never approved here
        result.saved_ids = r.add_requirements_with_sequential_ids(
            rows, prefix=ID_PREFIX, width=ID_WIDTH
        )
    logger.info("saved %d requirement(s) as pending, %d duplicate(s) skipped",
                len(result.saved_ids), len(result.duplicates))
    return result


def approve(requirement_id: str, *, repo: SQLiteRepository | None = None) -> Requirement:
    """Approve one requirement after re-checking its stored evidence.

    Raises RegistryError (status unchanged) if the requirement does not exist
    or its evidence is not valid now.
    """
    with _repo(repo) as r:
        row = _get(r, requirement_id)
        problems = evidence_validator.evidence_problems(row, r)
        if problems:
            raise RegistryError(
                f"{requirement_id} cannot be approved, invalid evidence: {'; '.join(problems)}"
            )
        try:
            r.set_requirement_status(requirement_id, "approved")  # re-checks in its transaction
        except RepositoryError as e:
            raise RegistryError(str(e)) from e
        return Requirement.model_validate(r.get_requirement(requirement_id))


# Reading and rejecting return repository rows (dicts), not Requirement models:
# a stored row may be broken (e.g. no evidence after a raw insert), and the
# reviewer must still be able to list it, see it, and reject it.


def reject(requirement_id: str, *, repo: SQLiteRepository | None = None) -> dict[str, Any]:
    """Reject one requirement (no evidence check needed). Returns the updated row."""
    with _repo(repo) as r:
        _get(r, requirement_id)
        r.set_requirement_status(requirement_id, "rejected")
        return _get(r, requirement_id)


def get(requirement_id: str, *, repo: SQLiteRepository | None = None) -> dict[str, Any]:
    """One stored row (repository shape, with "evidence"). RegistryError if missing."""
    with _repo(repo) as r:
        return _get(r, requirement_id)


def list_by_status(
    status: str | None = None, *, repo: SQLiteRepository | None = None
) -> list[dict[str, Any]]:
    """Stored rows, optionally filtered by status, in creation order."""
    with _repo(repo) as r:
        return r.list_requirements(status)


def get_approved(*, repo: SQLiteRepository | None = None) -> list[Requirement]:
    """Approved requirements for the SRS (app.py step 5).

    Evidence is re-checked as a last line of defence: the repository blocks
    every API path to an approved requirement with invalid evidence, but raw
    SQL (or a passage text changed outside the repository) can still produce
    one. If any approved requirement has invalid evidence, RegistryError is
    raised instead of silently building an SRS on it.
    """
    with _repo(repo) as r:
        rows = r.get_approved_requirements()
        bad = [(row["id"], p) for row in rows if (p := evidence_validator.evidence_problems(row, r))]
        if bad:
            details = "; ".join(f"{rid}: {', '.join(p)}" for rid, p in bad)
            raise RegistryError(
                f"{len(bad)} approved requirement(s) have invalid evidence: {details}. "
                "Fix the evidence or reject them, then approve again"
            )
        return [Requirement.model_validate(row) for row in rows]


# ------------------------------------------------------------------ helpers


_repo = evidence_validator.using_repo  # use the given repo or open config.DB_PATH


def _get(repo: SQLiteRepository, requirement_id: str) -> dict[str, Any]:
    row = repo.get_requirement(requirement_id)
    if row is None:
        raise RegistryError(f"Requirement {requirement_id!r} not found")
    return row


def _as_draft(candidate: RequirementDraft | Mapping[str, Any]) -> RequirementDraft:
    return evidence_validator.as_draft(candidate)


def _statement_key(statement: str) -> str:
    return " ".join(statement.casefold().split())


# ---------------------------------------------------------------------- CLI


def _main(argv: list[str]) -> int:
    usage = "usage: python -m services.requirement_registry list [STATUS] | show ID | approve ID... | reject ID..."
    if not argv:
        print(usage)
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "list" and len(args) <= 1:
            with _repo(None) as r:
                for row in r.list_requirements(args[0] if args else None):
                    where = row["feature"] or row["nfr_category"]
                    flag = "" if not evidence_validator.evidence_problems(row, r) else "  [INVALID EVIDENCE]"
                    print(f"{row['id']}  {row['status']:<8} {row['type'][:3].upper()} "
                          f"{row['priority']:<6} [{where}] {row['statement']}{flag}")
            return 0
        if command == "show" and len(args) == 1:
            with _repo(None) as r:
                row = _get(r, args[0])
                problems = evidence_validator.evidence_problems(row, r)
                texts = {p["id"]: p["text"] for p in r.get_passages([e["passage_id"] for e in row["evidence"]])}
            for key in ("id", "status", "type", "priority", "feature", "nfr_category", "statement",
                        "stimulus_or_condition", "response", "verification_method", "acceptance_measure"):
                if row.get(key) is not None:
                    print(f"{key:<22} {row[key]}")
            for e in row["evidence"]:
                print(f"evidence  {e['passage_id']}: quote {e['quote']!r}")
                print(f"          passage: {texts.get(e['passage_id'], '(missing)')[:200]}")
            print("evidence check:", "valid" if not problems else "INVALID: " + "; ".join(problems))
            return 0
    except RegistryError as e:
        print(f"ERROR {e}")
        return 1
    if command in ("approve", "reject") and args:
        action = approve if command == "approve" else reject
        failed = 0
        for requirement_id in args:
            try:
                result = action(requirement_id)
                status = result.status if isinstance(result, Requirement) else result["status"]
                print(f"{requirement_id}: {status}")
            except RegistryError as e:
                failed += 1
                print(f"ERROR {e}")
        return 1 if failed else 0
    print(usage)
    return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(_main(sys.argv[1:]))
