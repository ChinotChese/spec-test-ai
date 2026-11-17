"""Backfill projects into a database migrated to 002_projects, and review what is left.

Run from src/, after `python -m database.migrate up`:

    python -m database.project_backfill plan             # write a proposed mapping + preview
    python -m database.project_backfill apply            # apply the mapping (backup first)
    python -m database.project_backfill apply --dry-run  # report only, change nothing
    python -m database.project_backfill review           # what still needs a human decision
    python -m database.project_backfill projects         # projects, documents, counts

    python -m database.project_backfill assign RQ-0007 PRJ-a [--link-documents]
    python -m database.project_backfill assign-run <run id> PRJ-a
    python -m database.project_backfill link-document PRJ-a <document id>
    python -m database.project_backfill unlink-document PRJ-a <document id>

Common option: --db PATH (default: config.DB_PATH).

Which document belongs to which project is a human decision, so `apply`
reads it from a mapping file (default src/data/project_mapping.json, which is
gitignored with the database). `plan` proposes one project per document that
is not in any project yet; edit the file (merge documents into one project,
rename projects) before `apply`. Format:

    {"projects": [{"id": "PRJ-...", "name": "...", "description": null,
                   "document_ids": ["DOC-...", ...]}]}

`apply` then, in ONE transaction:
  1. creates the projects (an existing id must have the same name) and links
     their documents;
  2. assigns a requirement without a project ONLY when all its evidence comes
     from one document and that document belongs to exactly one project.
     Left unassigned (project_id NULL) for review:
       - evidence from several documents, even if they map to the same project
         (the reviewer confirms with `assign`);
       - no evidence;
       - a document in no project, or shared by several projects;
  3. assigns an SRS run without a project when all its requirements now
     belong to the same project; otherwise leaves it for review.
Running it again is safe: it only touches rows still without a project.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from database import migrate
from database.sqlite_repository import RepositoryError, SQLiteRepository

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class BackfillError(Exception):
    """The mapping or the command is invalid; nothing was changed."""


@dataclass
class Decision:
    """What the backfill does with one requirement or SRS run."""

    kind: str                         # "requirement" | "srs_run"
    id: str
    status: str | None = None         # requirement review status
    project_id: str | None = None     # set when it can be assigned automatically
    reason: str | None = None         # set when a human must decide
    document_ids: list[str] = field(default_factory=list)
    candidate_project_ids: list[str] = field(default_factory=list)


@dataclass
class Report:
    projects_created: list[str] = field(default_factory=list)
    links_added: int = 0
    assigned: list[Decision] = field(default_factory=list)
    needs_review: list[Decision] = field(default_factory=list)
    documents_without_project: list[str] = field(default_factory=list)
    scope_problems: list[str] = field(default_factory=list)


class _Rollback(Exception):
    pass


# ------------------------------------------------------------ classification


def classify_requirement(repo: SQLiteRepository, row: dict[str, Any]) -> Decision:
    rid = row["id"]
    docs = repo.requirement_document_ids(rid)
    d = Decision("requirement", rid, status=row["status"], document_ids=docs)
    if not docs:
        d.reason = "no evidence, so its source document (and project) is unknown"
        return d
    projects_per_doc = {doc: [p["id"] for p in repo.list_projects(document_id=doc)] for doc in docs}
    if len(docs) > 1:
        common = set.intersection(*(set(ps) for ps in projects_per_doc.values()))
        any_ = dict.fromkeys(p for ps in projects_per_doc.values() for p in ps)
        d.candidate_project_ids = [p for p in any_ if p in common] + [p for p in any_ if p not in common]
        d.reason = (
            f"cites {len(docs)} documents; confirm which project it belongs to"
            + (f" (all its documents are in {sorted(common)})" if common else
               " (no single project holds all of them)")
        )
        return d
    projects = projects_per_doc[docs[0]]
    d.candidate_project_ids = projects
    if not projects:
        d.reason = f"its document {docs[0]} is not linked to any project"
    elif len(projects) > 1:
        d.reason = f"its document {docs[0]} is shared by several projects"
    else:
        d.project_id = projects[0]
    return d


def classify_srs_run(repo: SQLiteRepository, run: dict[str, Any]) -> Decision:
    d = Decision("srs_run", run["id"])
    ids = run["requirement_ids"]
    if not ids:
        d.reason = "includes no requirements"
        return d
    owners = {rid: (repo.get_requirement(rid) or {}).get("project_id") for rid in ids}
    unassigned = [rid for rid, p in owners.items() if p is None]
    projects = list(dict.fromkeys(p for p in owners.values() if p is not None))
    d.candidate_project_ids = projects
    if unassigned:
        d.reason = f"includes requirements without a project: {unassigned}"
    elif len(projects) > 1:
        d.reason = f"includes requirements of several projects: {projects}"
    else:
        d.project_id = projects[0]
    return d


# ------------------------------------------------------------------ mapping


def default_mapping_path() -> Path:
    import config

    return config.DATA_DIR / "project_mapping.json"


def propose_mapping(repo: SQLiteRepository) -> dict[str, Any]:
    """One project per document that is not in any project yet."""
    taken = {p["name"] for p in repo.list_projects()}
    projects = []
    for doc in repo.list_documents():
        if repo.list_projects(document_id=doc["id"]):
            continue
        suffix = doc["id"].removeprefix("DOC-")
        name = Path(doc["filename"]).stem.strip() or doc["id"]
        if name in taken:
            name = f"{name} ({doc['id']})"
        taken.add(name)
        projects.append({
            "id": f"PRJ-{suffix}",
            "name": name,
            "description": f"Backfilled from document {doc['id']}",
            "document_ids": [doc["id"]],
        })
    return {"projects": projects}


def load_mapping(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BackfillError(f"{path} not found; create it with: python -m database.project_backfill plan")
    except json.JSONDecodeError as e:
        raise BackfillError(f"{path} is not valid JSON: {e}") from e
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        raise BackfillError(f'{path}: expected {{"projects": [...]}}')
    for p in projects:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"].strip() \
                or not isinstance(p.get("name"), str) or not p["name"].strip() \
                or not isinstance(p.get("document_ids", []), list):
            raise BackfillError(f"{path}: each project needs a non-empty id, name and a document_ids list: {p!r}")
    return projects


def _validate_mapping(repo: SQLiteRepository, projects: list[dict[str, Any]]) -> None:
    problems = []
    ids = [p["id"] for p in projects]
    names = [p["name"] for p in projects]
    problems += [f"project id {i!r} listed twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    problems += [f"project name {n!r} listed twice" for n in sorted({n for n in names if names.count(n) > 1})]
    known_docs = {d["id"] for d in repo.list_documents()}
    for p in projects:
        for doc in p.get("document_ids", []):
            if doc not in known_docs:
                problems.append(f"{p['id']}: unknown document {doc!r}")
        existing = repo.get_project(p["id"])
        if existing is not None and existing["name"] != p["name"]:
            problems.append(
                f"{p['id']} already exists with another name; use that name or a new id"
            )
        clash = repo.conn.execute(
            "SELECT id FROM projects WHERE name = ? AND id != ?", (p["name"], p["id"])
        ).fetchone()
        if clash:
            problems.append(f"project name {p['name']!r} is already used by {clash[0]}")
    if problems:
        raise BackfillError("Invalid mapping, nothing was changed:\n  " + "\n  ".join(problems))


# -------------------------------------------------------------------- apply


def apply(repo: SQLiteRepository, projects: list[dict[str, Any]], *, dry_run: bool = False) -> Report:
    """Create/link projects and assign what is unambiguous, in one transaction."""
    _require_projects_schema(repo)
    _validate_mapping(repo, projects)
    report = Report()
    conn = repo.conn
    try:
        with conn:
            for p in projects:
                if repo.get_project(p["id"]) is None:
                    conn.execute(
                        "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
                        (p["id"], p["name"], p.get("description")),
                    )
                    report.projects_created.append(p["id"])
                for doc in p.get("document_ids", []):
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO project_documents (project_id, document_id) "
                        "VALUES (?, ?)",
                        (p["id"], doc),
                    )
                    report.links_added += cur.rowcount

            for row in repo.list_requirements(unassigned=True):
                d = classify_requirement(repo, row)
                if d.project_id is None:
                    report.needs_review.append(d)
                    continue
                conn.execute(
                    f"UPDATE requirements SET project_id = ?, updated_at = {_NOW} WHERE id = ?",
                    (d.project_id, d.id),
                )
                if problems := repo.project_scope_problems(d.id):  # cannot happen; be sure
                    raise BackfillError(f"{d.id}: {'; '.join(problems)}")
                report.assigned.append(d)

            for run in repo.list_srs_runs(unassigned=True):
                d = classify_srs_run(repo, run)
                if d.project_id is None:
                    report.needs_review.append(d)
                    continue
                conn.execute("UPDATE srs_runs SET project_id = ? WHERE id = ?", (d.project_id, d.id))
                report.assigned.append(d)

            report.documents_without_project = _documents_without_project(repo)
            report.scope_problems = _scope_problems(repo)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise BackfillError(f"foreign key violations after backfill: {violations[:3]}")
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return report


def review(repo: SQLiteRepository) -> Report:
    """Read-only: everything that still has no project, and why."""
    _require_projects_schema(repo)
    report = Report()
    report.needs_review = [classify_requirement(repo, r) for r in repo.list_requirements(unassigned=True)]
    report.needs_review += [classify_srs_run(repo, r) for r in repo.list_srs_runs(unassigned=True)]
    report.documents_without_project = _documents_without_project(repo)
    report.scope_problems = _scope_problems(repo)
    return report


def _documents_without_project(repo: SQLiteRepository) -> list[str]:
    return [
        r[0]
        for r in repo.conn.execute(
            "SELECT id FROM documents WHERE id NOT IN (SELECT document_id FROM project_documents) "
            "ORDER BY created_at, rowid"
        )
    ]


def _scope_problems(repo: SQLiteRepository) -> list[str]:
    """Assigned requirements citing documents outside their project (e.g. raw SQL edits)."""
    rows = repo.conn.execute("SELECT id FROM requirements WHERE project_id IS NOT NULL ORDER BY id")
    return [f"{rid}: {p}" for (rid,) in rows.fetchall() for p in repo.project_scope_problems(rid)]


def _require_projects_schema(repo: SQLiteRepository) -> None:
    st = migrate.status(repo.conn)
    if st.current < 2:
        raise BackfillError("database is not migrated to 002_projects; run: python -m database.migrate up")


# ---------------------------------------------------------------------- cli


def _print_report(report: Report, repo: SQLiteRepository, *, title: str) -> None:
    docs = {d["id"]: d["filename"] for d in repo.list_documents()}
    print(title)
    if report.projects_created or report.links_added:
        print(f"  projects created: {len(report.projects_created)}   document links added: {report.links_added}")
    reqs = [d for d in report.assigned if d.kind == "requirement"]
    runs = [d for d in report.assigned if d.kind == "srs_run"]
    if report.assigned:
        print(f"  assigned: {len(reqs)} requirement(s), {len(runs)} SRS run(s)")
        for d in report.assigned:
            print(f"    {d.kind:<11} {d.id:<34} -> {d.project_id}")
    print(f"  needs review: {len(report.needs_review)}")
    for d in report.needs_review:
        status = f" [{d.status}]" if d.status else ""
        print(f"    {d.kind:<11} {d.id}{status}: {d.reason}")
        for doc in d.document_ids:
            print(f"        document {doc}  {docs.get(doc, '?')}")
        if d.candidate_project_ids:
            print(f"        candidate projects: {', '.join(d.candidate_project_ids)}")
    if report.documents_without_project:
        print(f"  documents in no project: {', '.join(report.documents_without_project)}")
    if report.scope_problems:
        print("  PROJECT SCOPE PROBLEMS (assigned requirement cites a document outside its project):")
        for p in report.scope_problems:
            print(f"    {p}")
    if report.needs_review:
        print(
            "\n  Decide each one, then:\n"
            "    assign <requirement id> <project id> [--link-documents]\n"
            "    assign-run <run id> <project id>\n"
            "  --link-documents adds the requirement's other documents to that project;\n"
            "  use it only when those documents really belong to the project."
        )


def _print_projects(repo: SQLiteRepository) -> None:
    docs = {d["id"]: d["filename"] for d in repo.list_documents()}
    projects = repo.list_projects()
    if not projects:
        print("No projects.")
    for p in projects:
        n_req = len(repo.list_requirements(project_id=p["id"]))
        n_run = len(repo.list_srs_runs(project_id=p["id"]))
        print(f"{p['id']}  {p['name']}   ({n_req} requirements, {n_run} SRS runs)")
        for doc in p["document_ids"]:
            print(f"    {doc}  {docs.get(doc, '?')}")


def main(argv: list[str] | None = None) -> int:
    import config

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="write a proposed mapping file and preview it")
    p_plan.add_argument("--mapping", type=Path, default=None)
    p_plan.add_argument("--force", action="store_true", help="overwrite an existing mapping file")

    p_apply = sub.add_parser("apply", help="apply the mapping and assign what is unambiguous")
    p_apply.add_argument("--mapping", type=Path, default=None)
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--no-backup", action="store_true")

    sub.add_parser("review", help="list what still has no project")
    sub.add_parser("projects", help="list projects")

    p_assign = sub.add_parser("assign", help="assign a requirement to a project")
    p_assign.add_argument("requirement_id")
    p_assign.add_argument("project_id")
    p_assign.add_argument("--link-documents", action="store_true",
                          help="also link the requirement's documents to the project")

    p_run = sub.add_parser("assign-run", help="assign an SRS run to a project")
    p_run.add_argument("run_id")
    p_run.add_argument("project_id")

    for name in ("link-document", "unlink-document"):
        p = sub.add_parser(name)
        p.add_argument("project_id")
        p.add_argument("document_id")

    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"{db_path} does not exist")
        return 1

    try:
        with SQLiteRepository(db_path) as repo:
            if args.command == "plan":
                path = args.mapping or default_mapping_path()
                if path.exists() and not args.force:
                    print(f"{path} exists; edit it, or pass --force to replace it with a new proposal")
                    mapping = {"projects": load_mapping(path)}
                else:
                    _require_projects_schema(repo)
                    mapping = propose_mapping(repo)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    print(f"Proposed mapping written to {path} (one project per document).")
                    print("Edit it if several documents belong to one project, then run `apply`.\n")
                report = apply(repo, mapping["projects"], dry_run=True)
                _print_report(report, repo, title="Preview (nothing changed):")
            elif args.command == "apply":
                projects = load_mapping(args.mapping or default_mapping_path())
                if not args.dry_run:
                    _validate_mapping(repo, projects)  # fail before making a backup
                    if not args.no_backup:
                        print(f"Backup: {migrate.backup(db_path)}")
                report = apply(repo, projects, dry_run=args.dry_run)
                _print_report(report, repo, title="Dry run (nothing changed):" if args.dry_run else "Applied:")
            elif args.command == "review":
                report = review(repo)
                _print_report(report, repo, title="Review:")
                return 1 if report.needs_review or report.scope_problems else 0
            elif args.command == "projects":
                _print_projects(repo)
            elif args.command == "assign":
                repo.set_requirement_project(
                    args.requirement_id, args.project_id, link_documents=args.link_documents
                )
                print(f"{args.requirement_id} -> {args.project_id}")
            elif args.command == "assign-run":
                repo.set_srs_run_project(args.run_id, args.project_id)
                print(f"{args.run_id} -> {args.project_id}")
            elif args.command == "link-document":
                if repo.get_project(args.project_id) is None:
                    raise RepositoryError(f"Project {args.project_id!r} not found")
                repo.link_document(args.project_id, args.document_id)
                print(f"linked {args.document_id} to {args.project_id}")
            elif args.command == "unlink-document":
                repo.unlink_document(args.project_id, args.document_id)
                print(f"unlinked {args.document_id} from {args.project_id}")
    except (BackfillError, RepositoryError) as e:
        print(f"Refused: {e}")
        if args.command == "assign" and "not linked to project" in str(e):
            print("If those documents belong to this project, re-run with --link-documents.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
