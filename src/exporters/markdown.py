"""Export a validated SRS to Markdown (src/output by default).

    path = markdown.export(srs, requirements)     # app.py step 7

The SRS is validated first (services.srs_validator); if validation fails,
SrsValidationError is raised and NO file is written. The whole document is
rendered in memory and then written atomically (temp file + rename).

Layout follows templates/IEEE_830_SRS_Template.md, with section numbers and
titles taken from the SRS's own table of contents:
  title block, table of contents, revision history, sections 1-6,
  Appendix A  Traceability matrix: SRS id -> registry id -> passage ->
              document, page -> stored quote, for every FR / NFR,
  Appendix B  Sources: every cited passage with its document, page and text,
  Appendix C  Validation notes (warnings).
Citations are links like [S3](#src-3) to Appendix B (plain anchors, so they
work in any Markdown preview). All dynamic text is escaped: "<Project Name>"
stays visible and "|" cannot break a table.

Command line (from src/):
    python -m exporters.markdown SRS.json --project PRJ-x [--out-dir DIR] [--name FILE.md]
"""

from __future__ import annotations

import html
import os
import re
import sys
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import config
from database.sqlite_repository import SQLiteRepository
from schemas.requirement import Requirement
from schemas.srs import SRS, Narrative
from services import srs_validator
from services.srs_validator import SrsValidationReport

# ------------------------------------------------------------------- public


def export(
    srs: SRS | Mapping[str, Any],
    requirements: Iterable[Requirement | Mapping[str, Any] | str] | None = None,
    *,
    requirement_map: Mapping[str, str] | None = None,
    out_dir: str | Path | None = None,
    filename: str | None = None,
    project_id: str | None = None,
    repo: SQLiteRepository | None = None,
) -> Path:
    """Validate, render and write the SRS; returns the path of the .md file.

    project_id: the SRS's project; when None it is taken from `requirements`
    (see services.srs_validator).
    """
    report = srs_validator.validate(
        srs, requirements, requirement_map=requirement_map, project_id=project_id, repo=repo
    )
    text = render(report)
    out_dir = Path(out_dir) if out_dir is not None else config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (filename or default_filename(report.srs))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def default_filename(srs: SRS) -> str:
    dc = srs.document_control
    return f"SRS_{_slug(dc.project_name)}_v{_slug(dc.version)}_{dc.date_created}.md"


def render(report: SrsValidationReport) -> str:
    return _Renderer(report).render()


# ---------------------------------------------------------------- renderer


class _Renderer:
    def __init__(self, report: SrsValidationReport):
        self.r = report
        self.srs = report.srs
        self.titles = {e.section_number: e.title for e in self.srs.table_of_contents}
        self.citation_no: dict[str, int] = {}   # passage id -> S-number, in order of first citation
        self.trace = {t.srs_id: t for t in report.trace}
        self.out: list[str] = []

    # -- helpers
    def w(self, line: str = "") -> None:
        self.out.append(line)

    def heading(self, level: int, number: str) -> None:
        anchor = "sec-" + number.replace(".", "-")
        self.w(f'<a id="{anchor}"></a>')
        self.w(f"{'#' * level} {number}. {md(self.titles[number])}")
        self.w()

    def cite(self, source_ids: Iterable[str]) -> str:
        links = []
        for sid in source_ids:
            n = self.citation_no.setdefault(sid, len(self.citation_no) + 1)
            links.append(f"[S{n}](#src-{n})")
        return (" " + " ".join(links)) if links else ""

    def narrative(self, n: Narrative) -> None:
        if n.status == "specified":
            self.w(f"{md(n.text)}{self.cite(n.source_ids)}")
        elif n.status == "unresolved":
            self.w(f"> **Unresolved:** {md(n.reason)}{self.cite(n.source_ids)}")
        else:
            self.w(f"> **Not applicable:** {md(n.reason)}{self.cite(n.source_ids)}")
        self.w()

    # -- document
    def render(self) -> str:
        self.front_matter()
        self.section1()
        self.section2()
        self.section3()
        self.section4()
        self.section5()
        self.section6()
        self.appendix_trace()
        self.appendix_sources()   # last: needs every citation number
        self.appendix_notes()
        return "\n".join(self.out).rstrip() + "\n"

    def front_matter(self) -> None:
        dc = self.srs.document_control
        self.w("# Software Requirements Specification")
        self.w()
        self.w(f"## for {md(dc.project_name)}")
        self.w()
        self.w(f"**Version:** {md(dc.version)}  ")
        self.w(f"**Prepared by:** {md(', '.join(dc.prepared_by))}  ")
        self.w(f"**Organization:** {md(dc.organization)}  ")
        self.w(f"**Date created:** {md(dc.date_created)}")
        self.w()
        self.w("---")
        self.w()
        self.w("## Table of Contents")
        self.w()
        for e in self.srs.table_of_contents:
            indent = "  " * e.section_number.count(".")
            self.w(f"{indent}- [{e.section_number}. {md(e.title)}](#sec-{e.section_number.replace('.', '-')})")
        self.w("- [Appendix A. Traceability Matrix](#appendix-a)")
        self.w("- [Appendix B. Sources](#appendix-b)")
        self.w("- [Appendix C. Validation Notes](#appendix-c)")
        self.w()
        self.w("## Revision History")
        self.w()
        self.table(["Name", "Date", "Reason for Changes", "Version"],
                   [[r.name, r.date, r.reason_for_changes, r.version] for r in self.srs.revision_history])
        self.w("---")
        self.w()

    def section1(self) -> None:
        intro = self.srs.introduction
        self.heading(2, "1")
        for i, name in enumerate(["purpose", "document_conventions",
                                  "intended_audience_and_reading_suggestions", "product_scope"], 1):
            self.heading(3, f"1.{i}")
            self.narrative(getattr(intro, name))
        self.heading(3, "1.5")
        if intro.references:
            self.table(["ID", "Title", "Author", "Version", "Date", "Location"],
                       [[r.reference_id, r.title, r.author or "", r.version or "", r.date or "",
                         r.location or ""] for r in intro.references])
        else:
            self.w("_No references._")
            self.w()

    def section2(self) -> None:
        od = self.srs.overall_description
        self.heading(2, "2")
        for i, name in enumerate(["product_perspective", "product_features", "user_classes_and_characteristics",
                                  "operating_environment", "design_and_implementation_constraints",
                                  "user_documentation", "assumptions_and_dependencies"], 1):
            self.heading(3, f"2.{i}")
            self.narrative(getattr(od, name))

    def section3(self) -> None:
        self.heading(2, "3")
        for f in self.srs.system_features:
            self.heading(3, f"3.{f.ordinal}")
            self.w(f"{md(f.description)}{self.cite(f.source_ids)}")
            self.w()

    def section4(self) -> None:
        ei = self.srs.external_interface_requirements
        self.heading(2, "4")
        for i, name in enumerate(["user_interfaces_overview", "hardware_interfaces", "software_interfaces"], 1):
            self.heading(3, f"4.{i}")
            self.narrative(getattr(ei, name))

    def section5(self) -> None:
        self.heading(2, "5")
        for m in self.srs.system_features_modules:
            n = m.ordinal
            self.heading(3, f"5.{n}")
            self.heading(4, f"5.{n}.1")
            self.w(md(m.description_and_priority.description))
            self.w()
            self.w(f"**Priority:** {md(m.description_and_priority.priority)}")
            self.w()
            self.heading(4, f"5.{n}.2")
            for s in m.stimulus_response_sequences:
                frs = [fr.requirement_id for fr in m.functional_requirements if s.sequence_id in fr.stimulus_response_ids]
                self.w(f"**{md(s.sequence_id)}**{self.cite(s.source_ids)}")
                self.w()
                self.w(f"- **Stimulus / condition:** {md(s.stimulus_or_condition)}")
                self.w(f"- **Response:** {md(s.response)}")
                self.w(f"- **Requirements:** {', '.join(f'[{x}](#{_req_anchor(x)})' for x in frs)}")
                self.w()
            self.heading(4, f"5.{n}.3")
            for fr in m.functional_requirements:
                self.requirement(fr)

    def section6(self) -> None:
        self.heading(2, "6")
        for g in self.srs.nfr_groups():
            self.heading(3, f"6.{g.ordinal}")
            if g.status != "specified":
                label = "Unresolved" if g.status == "unresolved" else "Not applicable"
                self.w(f"> **{label}:** {md(g.reason)}")
                self.w()
                continue
            for nf in g.requirements:
                self.requirement(nf, extra=[("Verification", nf.verification_method),
                                            ("Acceptance measure", nf.acceptance_measure)])

    def requirement(self, req, extra: list[tuple[str, str]] = ()) -> None:
        t = self.trace[req.requirement_id]
        self.w(f'<a id="{_req_anchor(req.requirement_id)}"></a>')
        self.w(f"**{req.requirement_id}:** {md(req.statement)}{self.cite(req.source_ids)}")
        self.w()
        self.w(f"- **Priority:** {md(req.priority)}")
        for label, value in extra:
            self.w(f"- **{label}:** {md(value)}")
        if hasattr(req, "stimulus_response_ids") and req.stimulus_response_ids:
            self.w(f"- **Stimulus/response:** {', '.join(md(x) for x in req.stimulus_response_ids)}")
        self.w(f"- **Origin:** registry {md(t.registry_id)} ([trace](#trace-{_req_anchor(req.requirement_id)}))")
        self.w()

    # -- appendices
    def appendix_trace(self) -> None:
        self.w('<a id="appendix-a"></a>')
        self.w("## Appendix A. Traceability Matrix")
        self.w()
        self.w("Every functional (REQ) and nonfunctional (NF) requirement, its approved registry "
               "requirement, and the source passages and quotes that support it. All rows were "
               "checked by `services.srs_validator` before export.")
        self.w()
        rows = []
        for t in self.r.trace:
            for i, e in enumerate(t.evidence):
                srs_cell = f'<a id="trace-{_req_anchor(t.srs_id)}"></a>{t.srs_id}' if i == 0 else t.srs_id
                n = self.citation_no.setdefault(e.passage_id, len(self.citation_no) + 1)
                where = f"{e.filename or e.document_id}" + (f", p. {e.page}" if e.page is not None else "")
                rows.append([_Raw(srs_cell), t.registry_id or "", t.type, t.priority,
                             _Raw(f"[S{n}](#src-{n}) {md(e.passage_id)}"), where, f"“{e.quote}”"])
        self.table(["SRS ID", "Registry ID", "Type", "Priority", "Source", "Document, page", "Stored quote"], rows)

    def appendix_sources(self) -> None:
        self.w('<a id="appendix-b"></a>')
        self.w("## Appendix B. Sources")
        self.w()
        for pid, n in sorted(self.citation_no.items(), key=lambda kv: kv[1]):
            p = self.r.passages.get(pid, {})
            d = self.r.documents.get(p.get("document_id"), {}) or {}
            page = f", page {p['page']}" if p.get("page") is not None else ""
            self.w(f'<a id="src-{n}"></a>')
            self.w(f"**[S{n}]** `{pid}` · {md(d.get('filename', 'unknown document'))}{page}")
            self.w()
            self.w(f"> {md(p.get('text', '(passage not found)'))}")
            self.w()

    def appendix_notes(self) -> None:
        self.w('<a id="appendix-c"></a>')
        self.w("## Appendix C. Validation Notes")
        self.w()
        traced = sum(1 for t in self.r.trace if t.registry_id)
        self.w(f"- {traced} of {len(self.r.trace)} requirements traced to approved registry requirements "
               f"with valid evidence; {len(self.citation_no)} source passages cited.")
        for w in self.r.warnings:
            self.w(f"- Warning: {md(w)}")
        self.w()

    def table(self, header: list[str], rows: list[list[Any]]) -> None:
        self.w("| " + " | ".join(md(h) for h in header) + " |")
        self.w("|" + "|".join(" --- " for _ in header) + "|")
        for row in rows:
            self.w("| " + " | ".join(cell(v) for v in row) + " |")
        self.w()


# ----------------------------------------------------------------- escaping


class _Raw(str):
    """Already-rendered Markdown that table cells must not escape again."""


_MD_SPECIAL = re.compile(r"([\\`*_\[\]])")


def md(text: Any) -> str:
    """Escape dynamic text for Markdown: HTML (<, >, &) and inline markup."""
    if text is None:
        return ""
    s = html.escape(str(text), quote=False)
    return _MD_SPECIAL.sub(r"\\\1", s)


def cell(value: Any) -> str:
    s = str(value) if isinstance(value, _Raw) else md(value)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _req_anchor(srs_id: str) -> str:
    """REQ-1.1 -> req-1-1, NF-2.1 -> nf-2-1."""
    return srs_id.lower().replace(".", "-")


def _slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "untitled"


# ---------------------------------------------------------------------- CLI


if __name__ == "__main__":
    import argparse
    import json
    import logging

    from app_logging import setup_cli_logging

    setup_cli_logging("markdown")
    parser = argparse.ArgumentParser(description="Validate an SRS JSON file and export it to Markdown.")
    parser.add_argument("srs_json")
    parser.add_argument("--project", required=True, help="project id the SRS was generated for")
    parser.add_argument("--out-dir", default=None, help=f"default: {config.OUTPUT_DIR}")
    parser.add_argument("--name", default=None, help="file name (default: SRS_<project>_v<version>_<date>.md)")
    args = parser.parse_args()

    from services import requirement_registry

    data = json.loads(Path(args.srs_json).read_text(encoding="utf-8"))
    try:
        path = export(data, requirement_registry.get_approved(project_id=args.project),
                      out_dir=args.out_dir, filename=args.name, project_id=args.project)
    except (srs_validator.SrsValidationError, requirement_registry.RegistryError) as e:
        for err in getattr(e, "errors", [str(e)]):
            logging.error("%s", err)
        sys.exit(1)
    print(path)
