"""Projects: create them, name them, and choose which documents they use.

Every requirement and SRS run belongs to one project, and a requirement may
only cite passages from its project's documents (database/spec.md §3.2).
A pipeline run therefore starts by creating a project (or picking one):

    python -m services.project_registry create "Customer portal"      # prints PRJ-...
    python -m services.document_loader --project PRJ-... [files...]   # ingest + link

Command line (from src/):
    python -m services.project_registry list
    python -m services.project_registry create NAME [--id PRJ-x] [--description TEXT]
    python -m services.project_registry rename PRJ-x NEW_NAME
    python -m services.project_registry link PRJ-x DOC-...          # add a stored document
    python -m services.project_registry unlink PRJ-x DOC-...        # refused while cited
    python -m services.project_registry delete PRJ-x                # refused while it owns rows
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any

from database.sqlite_repository import RepositoryError, SQLiteRepository
from services.evidence_validator import using_repo

ID_PREFIX = "PRJ-"


class ProjectError(Exception):
    """A project operation was refused; nothing was changed."""


def create(
    name: str,
    *,
    description: str | None = None,
    document_ids: list[str] | tuple[str, ...] = (),
    project_id: str | None = None,
    repo: SQLiteRepository | None = None,
) -> str:
    """Create a project (optionally with documents). Returns its id (PRJ-<12 hex> by default)."""
    if not name or not name.strip():
        raise ProjectError("Project name must not be empty")
    project_id = project_id or f"{ID_PREFIX}{uuid.uuid4().hex[:12]}"
    with using_repo(repo) as r:
        try:
            return r.add_project(
                name.strip(), description=description, document_ids=document_ids, project_id=project_id
            )
        except RepositoryError as e:
            raise ProjectError(str(e)) from e


def require(project_id: str, *, repo: SQLiteRepository | None = None) -> dict[str, Any]:
    """The project row, or ProjectError if it does not exist."""
    with using_repo(repo) as r:
        project = r.get_project(project_id)
    if project is None:
        raise ProjectError(
            f"Project {project_id!r} not found; list them with: python -m services.project_registry list"
        )
    return project


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.project_registry",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("create")
    p.add_argument("name")
    p.add_argument("--id", dest="project_id")
    p.add_argument("--description")
    p = sub.add_parser("rename")
    p.add_argument("project_id")
    p.add_argument("name")
    for command in ("link", "unlink"):
        p = sub.add_parser(command)
        p.add_argument("project_id")
        p.add_argument("document_id")
    p = sub.add_parser("delete")
    p.add_argument("project_id")
    args = parser.parse_args(argv)

    try:
        with using_repo(None) as r:
            if args.command == "list":
                docs = {d["id"]: d["filename"] for d in r.list_documents()}
                projects = r.list_projects()
                if not projects:
                    print("No projects. Create one with: python -m services.project_registry create NAME")
                for prj in projects:
                    n_req = len(r.list_requirements(project_id=prj["id"]))
                    n_run = len(r.list_srs_runs(project_id=prj["id"]))
                    print(f"{prj['id']}  {prj['name']}   ({n_req} requirements, {n_run} SRS runs)")
                    for doc in prj["document_ids"]:
                        print(f"    {doc}  {docs.get(doc, '?')}")
                unlinked = [d for d in docs if not r.list_projects(document_id=d)]
                if unlinked:
                    print(f"Documents in no project: {', '.join(unlinked)}")
            elif args.command == "create":
                print(create(args.name, description=args.description, project_id=args.project_id, repo=r))
            elif args.command == "rename":
                r.update_project(args.project_id, name=args.name)
                print(f"{args.project_id} renamed")
            elif args.command == "link":
                require(args.project_id, repo=r)
                if r.get_document(args.document_id) is None:
                    raise ProjectError(f"Document {args.document_id!r} not found")
                r.link_document(args.project_id, args.document_id)
                print(f"linked {args.document_id} to {args.project_id}")
            elif args.command == "unlink":
                r.unlink_document(args.project_id, args.document_id)
                print(f"unlinked {args.document_id} from {args.project_id}")
            elif args.command == "delete":
                require(args.project_id, repo=r)
                r.delete_project(args.project_id)
                print(f"deleted {args.project_id}")
    except (ProjectError, RepositoryError) as e:
        print(f"ERROR {e}")
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(_main(sys.argv[1:]))
