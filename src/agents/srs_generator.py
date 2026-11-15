"""Generate an IEEE 830 SRS (schemas.srs.SRS) from APPROVED requirements.

Who writes what:
  code   - sections 3, 5, 6: every feature, functional and nonfunctional
           requirement comes from the registry (statement, priority, NFR fields
           copied verbatim) and is numbered here (REQ-n.m, NF-n.m). Also
           document control, revision history, references, and the TOC.
  model  - (GEMINI_MODEL_GENERATOR, structured output) only prose: narrative
           sections 1.x / 2.x / 4.x, one description per feature, and the
           stimulus/response sequences of each feature, linked by registry id
           to that feature's requirements.

So the SRS can contain no requirement that is not an approved registry
requirement: the model has no field to write one. Every SRS requirement maps to
exactly one approved RQ- id (GenerationReport.requirement_map).

Checks on the model's answer (one retry with feedback, then GenerationError):
  - each listed feature present once, with a description and >= 1 sequence;
  - a sequence linking an id that is not a requirement of that feature is dropped;
  - narratives citing a passage id that was not sent, or "specified" without any
    source, become "unresolved" (with the reason), never kept as is.
The assembled document is validated with schemas.srs.SRS.

Usage:
    from agents import srs_generator
    srs = srs_generator.generate(requirements)            # app.py step 6
    report = srs_generator.generate_with_report(requirements, project_name="...")

    python -m agents.srs_generator [--project NAME] [--org ORG] [--author NAME] [--out FILE]
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, create_model

import config
from database.sqlite_repository import SQLiteRepository
from llm_clients import gemini_client
from prompts.srs_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas.requirement import Requirement
from schemas.srs import SRS, build_toc

logger = logging.getLogger(__name__)

NARRATIVE_SECTIONS: dict[str, tuple[str, str]] = {
    # field -> (SRS container, IEEE 830 section title); written by the model.
    # 1.2 Document Conventions is NOT here: it describes this document's own
    # numbering, which code knows exactly (see DOCUMENT_CONVENTIONS).
    "purpose": ("introduction", "1.1 Purpose"),
    "intended_audience_and_reading_suggestions": ("introduction", "1.3 Intended Audience and Reading Suggestions"),
    "product_scope": ("introduction", "1.4 Product Scope"),
    "product_perspective": ("overall_description", "2.1 Product Perspective"),
    "product_features": ("overall_description", "2.2 Product Features"),
    "user_classes_and_characteristics": ("overall_description", "2.3 User Classes and Characteristics"),
    "operating_environment": ("overall_description", "2.4 Operating Environment"),
    "design_and_implementation_constraints": ("overall_description", "2.5 Design and Implementation Constraints"),
    "user_documentation": ("overall_description", "2.6 User Documentation"),
    "assumptions_and_dependencies": ("overall_description", "2.7 Assumptions and Dependencies"),
    "user_interfaces_overview": ("external_interface_requirements", "4.1 User Interfaces Overview"),
    "hardware_interfaces": ("external_interface_requirements", "4.2 Hardware Interfaces"),
    "software_interfaces": ("external_interface_requirements", "4.3 Software Interfaces"),
}
DOCUMENT_CONVENTIONS = (
    "Section 3 introduces each system feature and section 5 specifies it, with the same "
    "feature numbering (3.N and 5.N). Functional requirements are identified as REQ-N.M "
    "(feature N, requirement M) and nonfunctional requirements as NF-N.M (category N of "
    "section 6, requirement M). Every requirement carries its own MoSCoW priority (must, "
    "should, could, wont); a feature's priority in 5.N.1 is the highest priority among its "
    "functional requirements. Stimulus/response sequences are identified as SR-N.M. "
    "Source ids refer to passages of the source documents listed in 1.5 References. "
    "Sections marked unresolved are not covered by the source documents."
)

_PRIORITY_RANK = {"must": 0, "should": 1, "could": 2, "wont": 3}
_MAX_ATTEMPTS = 2

PLACEHOLDERS = {  # same placeholders as templates/standard_srs.yaml
    "project_name": "<Project Name>",
    "organization": "<Organization>",
    "prepared_by": "<Author>",
}


class GenerationError(Exception):
    """No valid SRS could be generated; nothing was produced."""


# ------------------------------------------------- model output (generation schema)


class _NarrativeOut(BaseModel):
    status: Literal["specified", "unresolved", "not_applicable"]
    text: str | None = None
    reason: str | None = None
    source_ids: list[str] = Field(default_factory=list, description="Ids of the passages relied on.")


class _SequenceOut(BaseModel):
    stimulus_or_condition: str
    response: str
    requirement_ids: list[str] = Field(
        min_length=1, description="RQ- ids of this feature's requirements that this sequence restates."
    )


class _FeatureOut(BaseModel):
    feature: str = Field(description="Feature name exactly as given.")
    description: str = Field(description="1-3 sentences: what the feature provides.")
    sequences: list[_SequenceOut] = Field(min_length=1)


# What the model writes; everything else is built in code.
SrsDraft = create_model(
    "SrsDraft",
    **{name: (_NarrativeOut, Field(description=f"SRS section {title}."))
       for name, (_, title) in NARRATIVE_SECTIONS.items()},
    features=(list[_FeatureOut], Field(description="One entry per listed feature.")),
)

RESPONSE_SCHEMA: dict[str, Any] = SrsDraft.model_json_schema()


# ------------------------------------------------------------------- report


@dataclass
class GenerationReport:
    srs: SRS
    requirement_map: dict[str, str]          # SRS id (REQ-1.1 / NF-1.1) -> registry id (RQ-0013)
    documents: list[str]                     # source documents the requirements cite
    warnings: list[str] = field(default_factory=list)
    attempts: int = 1


# ------------------------------------------------------------------- public


def generate(
    requirements: Iterable[Requirement | Mapping[str, Any] | str],
    template: Mapping[str, Any] | None = None,
    **options: Any,
) -> SRS:
    """SRS for the given approved requirements (app.py step 6)."""
    return generate_with_report(requirements, template=template, **options).srs


def generate_with_report(
    requirements: Iterable[Requirement | Mapping[str, Any] | str],
    *,
    template: Mapping[str, Any] | None = None,
    project_name: str | None = None,
    organization: str | None = None,
    prepared_by: list[str] | str | None = None,
    version: str = "1.0",
    date: str | None = None,
    client: Any = None,
    model: str | None = None,
    repo: SQLiteRepository | None = None,
) -> GenerationReport:
    """Generate and validate an SRS; see the module docstring for the rules.

    `requirements`: approved requirements (Requirement, repository rows, or ids).
    They are re-read from the registry, which is the source of truth: every id
    must exist, be "approved", and have valid evidence.
    `template`: optional parsed templates/standard_srs.yaml; its document_control
    values are used as defaults. Document fields never come from the model.
    """
    own_repo = repo is None
    repo = repo or SQLiteRepository(config.DB_PATH)
    try:
        reqs = _load_approved(repo, requirements)
        functional = [r for r in reqs if r["type"] == "functional"]
        nonfunctional = [r for r in reqs if r["type"] == "nonfunctional"]
        if not functional:
            raise GenerationError(
                "An SRS needs at least one approved functional requirement "
                "(sections 3 and 5 require a feature)"
            )
        features = _group_features(functional)
        documents = _cited_documents(repo, reqs)
        passages = [p for doc in documents for p in repo.list_passages(doc["id"])]
    finally:
        if own_repo:
            repo.close()

    control = _document_control(template, project_name, organization, prepared_by, version, date)
    passage_ids = {p["id"] for p in passages}
    warnings: list[str] = []
    feedback: list[str] | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        draft = _ask_model(client, model, features, nonfunctional, passages, feedback)
        problems, attempt_warnings, cleaned = _check_draft(draft, features, passage_ids)
        if not problems:
            warnings.extend(attempt_warnings)
            break
        logger.warning("SRS draft attempt %d rejected: %s", attempt, "; ".join(problems))
        feedback = problems
    else:
        raise GenerationError(
            f"Model output still invalid after {_MAX_ATTEMPTS} attempts: {'; '.join(problems)}"
        )

    srs_dict, requirement_map = _assemble(control, cleaned, features, nonfunctional, documents)
    try:
        srs = SRS.model_validate(srs_dict)
    except ValidationError as e:  # a bug in assembly, not a model problem
        raise GenerationError(f"Assembled SRS does not validate: {e}") from e

    expected = {r["id"] for r in reqs}
    if sorted(requirement_map.values()) != sorted(expected) or set(requirement_map) != srs.requirement_ids():
        raise GenerationError("Requirement mapping is not one-to-one with the approved input")
    return GenerationReport(srs, requirement_map, [d["id"] for d in documents], warnings, attempt)


# --------------------------------------------------------------- inputs


def _load_approved(
    repo: SQLiteRepository, requirements: Iterable[Requirement | Mapping[str, Any] | str]
) -> list[dict[str, Any]]:
    ids = []
    for r in requirements:
        rid = r if isinstance(r, str) else (r.id if isinstance(r, Requirement) else r["id"])
        if rid not in ids:
            ids.append(rid)
    if not ids:
        raise GenerationError("No requirements given")
    rows, problems = [], []
    for rid in ids:
        row = repo.get_requirement(rid)
        if row is None:
            problems.append(f"{rid}: not in the registry")
        elif row["status"] != "approved":
            problems.append(f"{rid}: status is {row['status']!r}, not 'approved'")
        elif issues := repo.approval_problems(rid):
            problems.append(f"{rid}: invalid evidence ({'; '.join(issues)})")
        else:
            rows.append(row)
    if problems:
        raise GenerationError("Only approved requirements can be used: " + "; ".join(problems))
    return rows


def _group_features(functional: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in functional:  # input order -> feature order and numbering
        groups.setdefault(r["feature"], []).append(r)
    return list(groups.items())


def _cited_documents(repo: SQLiteRepository, reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passage_ids = [e["passage_id"] for r in reqs for e in r["evidence"]]
    doc_ids = list(dict.fromkeys(p["document_id"] for p in repo.get_passages(passage_ids)))
    return [repo.get_document(d) for d in doc_ids]


def _document_control(
    template: Mapping[str, Any] | None,
    project_name: str | None,
    organization: str | None,
    prepared_by: list[str] | str | None,
    version: str,
    date: str | None,
) -> dict[str, Any]:
    base = dict((template or {}).get("document_control") or {})
    authors = prepared_by if prepared_by is not None else base.get("prepared_by") or [PLACEHOLDERS["prepared_by"]]
    return {
        "project_name": project_name or base.get("project_name") or PLACEHOLDERS["project_name"],
        "version": version,
        "prepared_by": [authors] if isinstance(authors, str) else list(authors),
        "organization": organization or base.get("organization") or PLACEHOLDERS["organization"],
        "date_created": date or dt.date.today().isoformat(),
    }


# ----------------------------------------------------------------- model


def _ask_model(client, model, features, nonfunctional, passages, feedback) -> dict[str, Any]:
    try:
        text = gemini_client.generate_json(
            client,
            name="SRS",
            model=model or config.GEMINI_MODEL_GENERATOR,
            contents=[build_user_prompt(features, nonfunctional, passages, feedback)],
            system_instruction=SYSTEM_PROMPT,
            response_schema=RESPONSE_SCHEMA,
        )
    except gemini_client.GeminiError as e:
        raise GenerationError(str(e)) from e
    except _API_ERRORS as e:
        status = f" {e.status}" if e.status else ""
        raise GenerationError(f"SRS: Gemini HTTP {e.code}{status}: {e.message}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise GenerationError(f"SRS: Gemini response is not JSON: {e}") from e


def _api_error_types() -> tuple[type[BaseException], ...]:
    try:
        from google.genai import errors
    except ImportError:
        return ()
    return (errors.APIError,)


_API_ERRORS = _api_error_types()


def _check_draft(
    raw: Any, features: list[tuple[str, list[dict[str, Any]]]], passage_ids: set[str]
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Return (problems -> retry, warnings, cleaned draft)."""
    try:
        draft = SrsDraft.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        return [f"answer does not match the schema at {'.'.join(map(str, first['loc']))}: {first['msg']}"], [], {}

    problems: list[str] = []
    warnings: list[str] = []
    narratives: dict[str, dict[str, Any]] = {}
    for name, (_, title) in NARRATIVE_SECTIONS.items():
        n: _NarrativeOut = getattr(draft, name)
        unknown = [s for s in n.source_ids if s not in passage_ids]
        text = (n.text or "").strip() or None
        reason = (n.reason or "").strip() or None
        if n.status == "specified" and unknown:
            warnings.append(f"{title}: cited unknown passages {unknown}; set to unresolved")
            narratives[name] = _unresolved(f"Generated text cited passages that do not exist: {unknown}")
        elif n.status == "specified" and not text:
            problems.append(f"{title}: status 'specified' but no text")
        elif n.status == "specified" and not n.source_ids:
            warnings.append(f"{title}: specified without citing any passage; set to unresolved")
            narratives[name] = _unresolved("Generated text cited no source passage.")
        elif n.status == "specified":
            narratives[name] = {"status": "specified", "text": text, "reason": None,
                                "source_ids": list(dict.fromkeys(n.source_ids))}
        else:
            narratives[name] = {"status": n.status, "text": None,
                                "reason": reason or "Not stated in the source documents.",
                                "source_ids": [s for s in dict.fromkeys(n.source_ids) if s in passage_ids]}

    by_name: dict[str, _FeatureOut] = {}
    for f in draft.features:
        if f.feature in by_name:
            warnings.append(f"feature {f.feature!r} returned twice; first kept")
        else:
            by_name[f.feature] = f
    expected = {name for name, _ in features}
    for extra in by_name.keys() - expected:
        warnings.append(f"ignored feature not in the approved set: {extra!r}")

    cleaned_features: dict[str, dict[str, Any]] = {}
    for name, reqs in features:
        out = by_name.get(name)
        if out is None:
            problems.append(f"feature {name!r} is missing")
            continue
        own_ids = {r["id"] for r in reqs}
        sequences = []
        for s in out.sequences:
            bad = [rid for rid in s.requirement_ids if rid not in own_ids]
            if bad:
                warnings.append(f"feature {name!r}: dropped a sequence linking {bad} "
                                "(not requirements of this feature)")
                continue
            if not s.stimulus_or_condition.strip() or not s.response.strip():
                warnings.append(f"feature {name!r}: dropped an empty sequence")
                continue
            sequences.append({"stimulus_or_condition": s.stimulus_or_condition.strip(),
                              "response": s.response.strip(),
                              "requirement_ids": list(dict.fromkeys(s.requirement_ids))})
        if not out.description.strip():
            problems.append(f"feature {name!r} has no description")
        if not sequences:
            problems.append(f"feature {name!r} has no valid stimulus/response sequence "
                            f"(link one of: {sorted(own_ids)})")
        cleaned_features[name] = {"description": out.description.strip(), "sequences": sequences}
    return problems, warnings, {"narratives": narratives, "features": cleaned_features}


def _unresolved(reason: str) -> dict[str, Any]:
    return {"status": "unresolved", "text": None, "reason": reason, "source_ids": []}


# --------------------------------------------------------------- assembly


def _evidence_ids(reqs: Iterable[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(e["passage_id"] for r in reqs for e in r["evidence"]))


def _assemble(control, cleaned, features, nonfunctional, documents) -> tuple[dict[str, Any], dict[str, str]]:
    requirement_map: dict[str, str] = {}
    overviews, modules = [], []
    for ordinal, (name, reqs) in enumerate(features, 1):
        text = cleaned["features"][name]
        sequences, fr_sequence_ids = [], {r["id"]: [] for r in reqs}
        by_id = {r["id"]: r for r in reqs}
        for k, s in enumerate(text["sequences"], 1):
            sid = f"SR-{ordinal}.{k}"
            linked = [by_id[rid] for rid in s["requirement_ids"]]
            sequences.append({"sequence_id": sid, "stimulus_or_condition": s["stimulus_or_condition"],
                              "response": s["response"], "source_ids": _evidence_ids(linked)})
            for rid in s["requirement_ids"]:
                fr_sequence_ids[rid].append(sid)
        frs = []
        for m, r in enumerate(reqs, 1):
            srs_id = f"REQ-{ordinal}.{m}"
            requirement_map[srs_id] = r["id"]
            frs.append({"requirement_id": srs_id, "statement": r["statement"], "priority": r["priority"],
                        "stimulus_response_ids": fr_sequence_ids[r["id"]],
                        "source_ids": _evidence_ids([r])})
        overviews.append({"feature_id": f"FEAT-{ordinal:03d}", "ordinal": ordinal, "name": name,
                          "description": text["description"], "source_ids": _evidence_ids(reqs)})
        modules.append({
            "feature_id": f"FEAT-{ordinal:03d}", "ordinal": ordinal, "name": name,
            "description_and_priority": {
                "description": text["description"],
                "priority": min((r["priority"] for r in reqs), key=_PRIORITY_RANK.__getitem__),
            },
            "stimulus_response_sequences": sequences,
            "functional_requirements": frs,
        })

    groups = _nfr_groups(nonfunctional, requirement_map)
    narratives = cleaned["narratives"]
    srs = {
        "document_control": control,
        "table_of_contents": build_toc(
            [(f["ordinal"], f["name"]) for f in overviews],
            [(g["ordinal"], g["name"]) for g in groups],
        ),
        "revision_history": [{
            "name": control["prepared_by"][0], "date": control["date_created"],
            "reason_for_changes": "Initial version generated from approved requirements",
            "version": control["version"],
        }],
        "introduction": {**{k: v for k, v in narratives.items() if NARRATIVE_SECTIONS[k][0] == "introduction"},
                         "document_conventions": {"status": "specified", "text": DOCUMENT_CONVENTIONS,
                                                  "reason": None, "source_ids": []},
                         "references": _references(documents)},
        "overall_description": {k: v for k, v in narratives.items()
                                if NARRATIVE_SECTIONS[k][0] == "overall_description"},
        "system_features": overviews,
        "external_interface_requirements": {k: v for k, v in narratives.items()
                                            if NARRATIVE_SECTIONS[k][0] == "external_interface_requirements"},
        "system_features_modules": modules,
        "nonfunctional_requirements": {"performance": groups[0], "security": groups[1],
                                       "additional_categories": groups[2:]},
    }
    return srs, requirement_map


def _nfr_groups(nonfunctional: list[dict[str, Any]], requirement_map: dict[str, str]) -> list[dict[str, Any]]:
    by_key: dict[str, tuple[str, list[dict[str, Any]]]] = {
        "performance": ("Performance", []), "security": ("Security", []),
    }
    for r in nonfunctional:  # categories matched case-insensitively; first spelling wins
        key = " ".join(r["nfr_category"].casefold().split())
        by_key.setdefault(key, (r["nfr_category"].strip(), []))[1].append(r)
    groups = []
    for ordinal, (name, reqs) in enumerate(by_key.values(), 1):
        items = []
        for m, r in enumerate(reqs, 1):
            srs_id = f"NF-{ordinal}.{m}"
            requirement_map[srs_id] = r["id"]
            items.append({"requirement_id": srs_id, "statement": r["statement"], "priority": r["priority"],
                          "verification_method": r["verification_method"],
                          "acceptance_measure": r["acceptance_measure"], "source_ids": _evidence_ids([r])})
        groups.append({"ordinal": ordinal, "name": name,
                       "status": "specified" if items else "unresolved",
                       "reason": None if items else f"No approved {name.lower()} requirements.",
                       "requirements": items})
    return groups


def _references(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"reference_id": f"REF-{i:03d}", "title": d["filename"], "author": None, "version": None,
             "date": None, "location": d["file_path"], "source_ids": []}
            for i, d in enumerate(documents, 1)]


# ---------------------------------------------------------------------- CLI


if __name__ == "__main__":
    import argparse

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate an SRS from approved requirements.")
    parser.add_argument("--project")
    parser.add_argument("--org")
    parser.add_argument("--author", action="append")
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--out", help="write the SRS JSON here (default: print)")
    args = parser.parse_args()

    from services import requirement_registry

    try:
        report = generate_with_report(
            requirement_registry.get_approved(), project_name=args.project,
            organization=args.org, prepared_by=args.author, version=args.version,
        )
    except (GenerationError, requirement_registry.RegistryError) as e:
        logger.error("%s", e)
        sys.exit(1)
    for w in report.warnings:
        logger.warning("%s", w)
    logger.info("SRS: %d features, %d requirements, documents %s, attempts %d",
                len(report.srs.system_features), len(report.requirement_map),
                ", ".join(report.documents), report.attempts)
    payload = report.srs.model_dump_json(indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        logger.info("written to %s", args.out)
    else:
        print(payload)
