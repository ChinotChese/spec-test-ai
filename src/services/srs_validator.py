"""Validate a generated SRS and trace every requirement back to its source.

    report = srs_validator.validate(srs, requirements)      # app.py step 7

An SRS belongs to ONE project: `project_id`, or the common project of the
given `requirements` / `requirement_map`. Origins are looked up only among
that project's approved requirements, so two projects may share a statement.
A project that cannot be determined, or input spanning several projects, is
an error.

For every FR (REQ-n.m) and NFR (NF-n.m) in the SRS, the validator finds its
origin in the registry and checks it, because the SRS schema has no room for
registry ids:
  1. exactly one APPROVED requirement of the project has the same type and
     the exact same statement (the generator copies statements verbatim);
  2. its priority (and, for NFRs, category-independent fields
     verification_method / acceptance_measure) are equal;
  3. the SRS source_ids equal that requirement's evidence passage ids;
  4. its stored evidence is still valid (every quote found in its passage).
Every other source id in the document (narratives, features, sequences) must
be an existing passage.

Errors make validate() raise SrsValidationError. Warnings do not:
  - leftover template placeholders such as "<Project Name>" (app.py passes no
    project name, so this cannot block the default path);
  - unresolved / not-applicable sections and NFR groups;
  - approved requirements given in `requirements` that the SRS does not use.

The returned report carries the trace (SRS id -> registry id -> passages,
documents, pages, stored quotes) that exporters.markdown prints.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from database.sqlite_repository import SQLiteRepository
from schemas.requirement import Requirement
from schemas.srs import SRS
from services.evidence_validator import using_repo

# Template placeholders such as "<Project Name>"; e-mail addresses like <a@b.de> are not.
PLACEHOLDER = re.compile(r"<(?![^<>\n]*@)[^<>\n]{1,80}>")


class SrsValidationError(Exception):
    def __init__(self, errors: list[str], report: SrsValidationReport | None = None):
        self.errors = errors
        self.report = report
        shown = "; ".join(errors[:5]) + (f"; ... ({len(errors) - 5} more)" if len(errors) > 5 else "")
        super().__init__(f"SRS failed validation ({len(errors)} error(s)): {shown}")


@dataclass
class TracedEvidence:
    passage_id: str
    document_id: str | None
    filename: str | None
    page: int | None
    quote: str | None
    passage_text: str | None


@dataclass
class TraceEntry:
    srs_id: str                 # REQ-1.1 / NF-2.1
    registry_id: str | None     # RQ-0013, None when no origin was found
    type: str                   # functional / nonfunctional
    statement: str
    priority: str
    evidence: list[TracedEvidence] = field(default_factory=list)


@dataclass
class SrsValidationReport:
    srs: SRS
    project_id: str | None = None                                       # the SRS's project
    trace: list[TraceEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passages: dict[str, dict[str, Any]] = field(default_factory=dict)   # every cited passage
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)  # their documents

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def requirement_map(self) -> dict[str, str]:
        return {t.srs_id: t.registry_id for t in self.trace if t.registry_id}


# ------------------------------------------------------------------- public


def validate(
    srs: SRS | Mapping[str, Any],
    requirements: Iterable[Requirement | Mapping[str, Any] | str] | None = None,
    *,
    requirement_map: Mapping[str, str] | None = None,
    project_id: str | None = None,
    repo: SQLiteRepository | None = None,
) -> SrsValidationReport:
    """Validate and trace; raise SrsValidationError if anything is wrong."""
    report = check(srs, requirements, requirement_map=requirement_map, project_id=project_id, repo=repo)
    if report.errors:
        raise SrsValidationError(report.errors, report)
    return report


def check(
    srs: SRS | Mapping[str, Any],
    requirements: Iterable[Requirement | Mapping[str, Any] | str] | None = None,
    *,
    requirement_map: Mapping[str, str] | None = None,
    project_id: str | None = None,
    repo: SQLiteRepository | None = None,
) -> SrsValidationReport:
    """Same checks as validate(), but returns the report instead of raising.

    `requirements`: the approved requirements the SRS is expected to come from
    (app.py passes requirement_registry.get_approved(project_id=...)). Origins
    are looked up among ALL approved rows of the SRS's project; `requirements`
    also determines that project when `project_id` is not given, and is used to
    warn about approved requirements the SRS leaves out.
    `requirement_map`: optional {SRS id: registry id} from the generator; when
    given, it must agree with the traced origins.
    """
    try:  # re-validate: catches a dict or an SRS changed after construction
        doc = SRS.model_validate(srs.model_dump() if isinstance(srs, SRS) else srs)
    except ValidationError as e:
        raise SrsValidationError([f"not a valid SRS: {err['msg']} at {'.'.join(map(str, err['loc']))}"
                                  for err in e.errors()[:10]]) from e
    report = SrsValidationReport(doc)

    with using_repo(repo) as r:
        report.project_id = _resolve_project(r, project_id, requirements, requirement_map, report.errors)
        if report.project_id is None:
            return report  # tracing against an unknown project would only add noise
        approved = r.get_approved_requirements(report.project_id)
        by_statement: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in approved:
            by_statement.setdefault((row["type"], row["statement"]), []).append(row)

        for srs_req, kind in _requirements_of(doc):
            report.trace.append(_trace_one(srs_req, kind, by_statement, r, report.errors))

        cited = _all_source_ids(doc)
        passages = {p["id"]: p for p in r.get_passages(sorted(cited))}
        for sid in sorted(cited - passages.keys()):
            report.errors.append(f"source id {sid} is not a passage in the database")
        report.passages = passages
        report.documents = {d: r.get_document(d) for d in dict.fromkeys(p["document_id"] for p in passages.values())}

    for t in report.trace:  # attach document / page / passage text to the trace
        for e in t.evidence:
            p = report.passages.get(e.passage_id)
            if p:
                d = report.documents.get(p["document_id"]) or {}
                e.document_id, e.filename, e.page, e.passage_text = p["document_id"], d.get("filename"), p["page"], p["text"]

    _check_map(report, requirement_map)
    _check_coverage(report, requirements)
    _warn_placeholders_and_unresolved(doc, report.warnings)
    return report


# ----------------------------------------------------------------- tracing


def _requirements_of(doc: SRS):
    for m in doc.system_features_modules:
        for fr in m.functional_requirements:
            yield fr, "functional"
    for g in doc.nfr_groups():
        for nf in g.requirements:
            yield nf, "nonfunctional"


def _trace_one(srs_req, kind, by_statement, repo, errors) -> TraceEntry:
    entry = TraceEntry(srs_req.requirement_id, None, kind, srs_req.statement, srs_req.priority)
    matches = by_statement.get((kind, srs_req.statement), [])
    sid = srs_req.requirement_id
    if not matches:
        errors.append(f"{sid}: no approved {kind} requirement in the registry has this exact statement")
        return entry
    if len(matches) > 1:
        errors.append(f"{sid}: statement matches several approved requirements {[m['id'] for m in matches]}")
        return entry
    row = matches[0]
    entry.registry_id = row["id"]
    entry.evidence = [TracedEvidence(e["passage_id"], None, None, None, e["quote"], None) for e in row["evidence"]]

    if srs_req.priority != row["priority"]:
        errors.append(f"{sid} ({row['id']}): priority {srs_req.priority!r} != registry {row['priority']!r}")
    if kind == "nonfunctional":
        for f in ("verification_method", "acceptance_measure"):
            if getattr(srs_req, f) != row[f]:
                errors.append(f"{sid} ({row['id']}): {f} differs from the registry")
    evidence_ids = list(dict.fromkeys(e["passage_id"] for e in row["evidence"]))
    if list(srs_req.source_ids) != evidence_ids:
        errors.append(f"{sid} ({row['id']}): source_ids {list(srs_req.source_ids)} != evidence {evidence_ids}")
    if problems := repo.approval_problems(row["id"]):
        errors.append(f"{sid} ({row['id']}): invalid evidence: {'; '.join(problems)}")
    return entry


def _ids_of(requirements) -> list[str]:
    return [r if isinstance(r, str) else (r.id if isinstance(r, Requirement) else r["id"])
            for r in requirements or ()]


def _resolve_project(repo, project_id, requirements, requirement_map, errors) -> str | None:
    """The SRS's project: explicit, or the one project all given requirements share."""
    given = list(dict.fromkeys(_ids_of(requirements) + list((requirement_map or {}).values())))
    owners = {rid: (repo.get_requirement(rid) or {}).get("project_id") for rid in given}
    if project_id is not None:
        if repo.get_project(project_id) is None:
            errors.append(f"project {project_id} does not exist")
            return None
        if foreign := [rid for rid, p in owners.items() if p != project_id]:
            errors.append(f"requirements not in project {project_id}: {', '.join(foreign)}")
        return project_id
    if not given:
        errors.append("cannot tell which project the SRS belongs to: pass project_id or requirements")
        return None
    if unknown := [rid for rid, p in owners.items() if p is None]:  # id not in the registry
        errors.append(f"requirements not in the registry: {', '.join(unknown)}")
        return None
    projects = sorted(set(owners.values()))
    if len(projects) > 1:
        errors.append(f"requirements span several projects ({', '.join(projects)}); one SRS = one project")
        return None
    return projects[0]


def _all_source_ids(doc: SRS) -> set[str]:
    return doc.source_ids()


def _check_map(report: SrsValidationReport, requirement_map: Mapping[str, str] | None) -> None:
    if requirement_map is None:
        return
    traced = report.requirement_map
    for srs_id, rid in requirement_map.items():
        if srs_id not in {t.srs_id for t in report.trace}:
            report.errors.append(f"requirement_map names {srs_id}, which is not in the SRS")
        elif traced.get(srs_id) and traced[srs_id] != rid:
            report.errors.append(f"{srs_id}: requirement_map says {rid}, traced origin is {traced[srs_id]}")


def _check_coverage(report: SrsValidationReport, requirements) -> None:
    if requirements is None:
        return
    given = _ids_of(requirements)
    used = set(report.requirement_map.values())
    missing = [rid for rid in dict.fromkeys(given) if rid not in used]
    if missing:
        report.warnings.append(
            f"{len(missing)} approved requirement(s) are not in this SRS: {', '.join(missing[:10])}"
            + (" ..." if len(missing) > 10 else "")
        )


def _warn_placeholders_and_unresolved(doc: SRS, warnings: list[str]) -> None:
    def walk(value, path):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")
        elif isinstance(value, str) and (m := PLACEHOLDER.search(value)):
            warnings.append(f"placeholder {m.group(0)} left in {path}")

    walk(doc.model_dump(exclude={"table_of_contents"}), "")
    for container in ("introduction", "overall_description", "external_interface_requirements"):
        for name, value in getattr(doc, container):
            if hasattr(value, "status") and value.status != "specified":
                warnings.append(f"{container}.{name} is {value.status}: {value.reason}")
    for g in doc.nfr_groups():
        if g.status != "specified":
            warnings.append(f"6.{g.ordinal} {g.name} is {g.status}: {g.reason}")
