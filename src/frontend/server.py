"""Local demo UI for the SpecTestAI pipeline (standard library only).

    cd src
    uv run python -m frontend.server            # http://127.0.0.1:8765
    uv run python -m frontend.server --port 9000 --open

The page walks through every step of the pipeline for one project:

    0  project              services.project_registry
    1  upload + ingest      services.document_loader      (file <= 10 MB)
    2  index (optional)     retrieval.indexer
    3  extract              agents.requirement_extractor
    4  evidence + save      services.evidence_validator, services.requirement_registry
    5  review               services.requirement_registry approve / reject
    6  generate SRS         agents.srs_generator
    7  validate + export    services.srs_validator, exporters.markdown

Long steps (every Gemini / Qdrant call) run as background jobs, one at a
time; the page polls /api/jobs/<id> and shows the job's log lines live.
Bound to 127.0.0.1 by default: it is a demo for the local machine, with no
authentication. Uploaded files go to config.INPUT_DIR (gitignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import re
import socket
import threading
import time
import traceback
import unicodedata
import uuid
import webbrowser
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

import app_logging
import config
from database.sqlite_repository import SQLiteRepository

logger = logging.getLogger("frontend")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
MAX_JOB_LOG_LINES = 2000


# ---------------------------------------------------------------- jobs


class Job:
    def __init__(self, kind: str, project_id: str | None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.project_id = project_id
        self.status = "running"          # running / done / error
        self.logs: list[dict[str, Any]] = []
        self.result: Any = None
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = None

    def view(self, since: int = 0) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "project_id": self.project_id, "status": self.status,
            "logs": self.logs[since:], "log_count": len(self.logs),
            "result": self.result if self.status == "done" else None, "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
        }


JOBS: dict[str, Job] = {}
_JOB_LOCK = threading.Lock()
_CURRENT: dict[str, Job | None] = {"job": None}
STATE: dict[str, dict[str, Any]] = {}  # per project, in memory: last drafts, last SRS report


class BusyError(Exception):
    pass


class JobLogHandler(logging.Handler):
    """Copies log records of the running job into job.logs."""

    def emit(self, record: logging.LogRecord) -> None:
        job = _CURRENT["job"]
        if job is None or record.name.startswith(("httpx", "httpcore", "urllib3")):
            return
        if len(job.logs) >= MAX_JOB_LOG_LINES:
            return
        try:
            job.logs.append({
                "t": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "source": record.name,
                "msg": record.getMessage(),
            })
        except Exception:  # never let logging break a job
            pass


def install_log_capture() -> None:
    """Route INFO+ records of the running job into job.logs (idempotent)."""
    root = logging.getLogger()
    if not any(isinstance(h, JobLogHandler) for h in root.handlers):
        root.addHandler(JobLogHandler(level=logging.INFO))
    if root.getEffectiveLevel() > logging.INFO:
        root.setLevel(logging.INFO)


def start_job(kind: str, project_id: str | None, fn: Callable[[Job], Any]) -> Job:
    if not _JOB_LOCK.acquire(blocking=False):
        running = _CURRENT["job"]
        raise BusyError(f"Another step is still running ({running.kind if running else '?'}); wait for it")
    job = Job(kind, project_id)
    JOBS[job.id] = job
    _CURRENT["job"] = job

    def run() -> None:
        try:
            logger.info("start %s%s", kind, f" for {project_id}" if project_id else "")
            job.result = to_jsonable(fn(job))
            job.status = "done"
            logger.info("done %s in %.1fs", kind, time.time() - job.started)
        except Exception as e:  # shown in the page
            job.error = f"{type(e).__name__}: {e}"
            job.status = "error"
            logger.error("%s failed: %s", kind, e)
            logger.debug("%s", traceback.format_exc())
        finally:
            job.finished = time.time()
            _CURRENT["job"] = None
            _JOB_LOCK.release()

    threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
    return job


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def project_state(project_id: str) -> dict[str, Any]:
    return STATE.setdefault(project_id, {})


def repo() -> SQLiteRepository:
    return SQLiteRepository(config.DB_PATH)


# ---------------------------------------------------------------- steps


def step_ingest(project_id: str, path: Path) -> Callable[[Job], Any]:
    def run(job: Job) -> Any:
        from services import document_loader

        with repo() as r:
            result = document_loader.ingest_file(path, project_id=project_id, repo=r)
        document_loader._log_result(result)
        return {
            "filename": result.filename,
            "document_id": result.document_id,
            "skipped": result.skipped,
            "passage_count": len(result.passages),
            "rejected": [p.model_dump(mode="json") for p in result.rejected],
            "unverified": result.unverified,
            "coverage": result.coverage,
            "passages": [_passage_view(p) for p in result.passages],
        }
    return run


def step_index(project_id: str) -> Callable[[Job], Any]:
    def run(job: Job) -> Any:
        from retrieval import indexer

        report = indexer.index_passages(project_id=project_id)
        indexer._log_report(report)
        return report
    return run


def step_extract(project_id: str, document_ids: list[str]) -> Callable[[Job], Any]:
    def run(job: Job) -> Any:
        from agents import requirement_extractor

        with repo() as r:
            linked = {d["id"] for d in r.list_project_documents(project_id)}
            chosen = [d for d in document_ids if d in linked] or sorted(linked)
            if not chosen:
                raise ValueError("The project has no documents; upload one first (step 1)")
            passages = [p for d in chosen for p in r.list_passages(d)]
        logger.info("extracting from %d document(s), %d passages", len(chosen), len(passages))
        reports = requirement_extractor.extract_with_report(passages)
        drafts = [d for rep in reports for d in rep.requirements]
        project_state(project_id)["drafts"] = drafts
        return {
            "documents": [{
                "document_id": rep.document_id,
                "passage_count": rep.passage_count,
                "functional": len(rep.functional),
                "nonfunctional": len(rep.nonfunctional),
                "rejected": [{"item": to_jsonable(x.item), "reason": x.reason} for x in rep.rejected],
                "error": rep.error,
            } for rep in reports],
            "requirements": [d.model_dump(mode="json") for d in drafts],
        }
    return run


def step_validate_save(project_id: str) -> Callable[[Job], Any]:
    def run(job: Job) -> Any:
        from services import evidence_validator, requirement_registry

        drafts = project_state(project_id).get("drafts")
        if drafts is None:
            raise ValueError("No extracted requirements in this session; run step 3 first")
        report = evidence_validator.validate_with_report(drafts)
        saved = requirement_registry.save_candidates(report.valid, project_id=project_id)
        project_state(project_id)["drafts"] = None  # saved: do not save the same batch twice
        return {
            "valid": len(report.valid),
            "invalid": [{"statement": i.candidate.statement, "problems": i.problems} for i in report.invalid],
            "saved_ids": saved.saved_ids,
            "duplicates": [{"statement": s, "existing_id": e} for s, e in saved.duplicates],
        }
    return run


def step_generate(project_id: str, organization: str | None, authors: list[str],
                  version: str) -> Callable[[Job], Any]:
    def run(job: Job) -> Any:
        from agents import srs_generator
        from services import requirement_registry

        approved = requirement_registry.get_approved(project_id=project_id)
        if not approved:
            raise ValueError("No approved requirements; approve some in step 5 first")
        logger.info("%d approved requirement(s)", len(approved))
        report = srs_generator.generate_with_report(
            approved, organization=organization or None, prepared_by=authors or None,
            version=version or "1.0")
        for w in report.warnings:
            logger.warning("%s", w)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = config.OUTPUT_DIR / f"srs_{project_id}.json"
        json_path.write_text(report.srs.model_dump_json(indent=2), encoding="utf-8")
        logger.info("SRS JSON written to %s", json_path.name)
        project_state(project_id)["srs"] = {"json_path": json_path, "requirement_map": report.requirement_map}
        srs = report.srs
        return {
            "json_file": json_path.name,
            "features": len(srs.system_features),
            "requirements": len(report.requirement_map),
            "requirement_map": report.requirement_map,
            "documents": report.documents,
            "warnings": report.warnings,
            "attempts": report.attempts,
            "core_context_version": report.core_context_version,
            "evidence": report.evidence,
            "cache": report.cache,
            "tokens": {"prompt": report.prompt_tokens, "cached": report.cached_tokens,
                       "output": sum(u.output_tokens for u in report.usage), "calls": len(report.usage)},
            "srs": srs.model_dump(mode="json"),
        }
    return run


def step_export(project_id: str) -> Callable[[Job], Any]:
    def run(job: Job) -> Any:
        from exporters import markdown
        from services import requirement_registry, srs_validator

        saved = project_state(project_id).get("srs") or {}
        json_path = saved.get("json_path") or config.OUTPUT_DIR / f"srs_{project_id}.json"
        if not Path(json_path).is_file():
            raise ValueError("No generated SRS for this project; run step 6 first")
        srs = json.loads(Path(json_path).read_text(encoding="utf-8"))
        approved = requirement_registry.get_approved(project_id=project_id)
        mapping = saved.get("requirement_map")
        try:
            report = srs_validator.validate(srs, approved, requirement_map=mapping, project_id=project_id)
        except srs_validator.SrsValidationError as e:
            logger.error("SRS failed validation, no file written")
            return {"ok": False, "errors": e.errors, "warnings": e.report.warnings if e.report else []}
        logger.info("SRS validated: %d requirements traced, %d warning(s)", len(report.trace), len(report.warnings))
        path = markdown.export(srs, approved, requirement_map=mapping, project_id=project_id)
        logger.info("Markdown written to %s", path.name)
        return {
            "ok": True,
            "file": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "warnings": report.warnings,
            "trace": [{"srs_id": t.srs_id, "registry_id": t.registry_id, "type": t.type,
                       "statement": t.statement, "priority": t.priority,
                       "sources": [f"{e.passage_id}" + (f" (p.{e.page})" if e.page else "") for e in t.evidence]}
                      for t in report.trace],
            "markdown": path.read_text(encoding="utf-8"),
        }
    return run


# ---------------------------------------------------------- read helpers


def _passage_view(p: dict[str, Any]) -> dict[str, Any]:
    return {"id": p["id"], "page": p.get("page"), "text": p.get("text")}


def status_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "gemini_key": bool(config.GEMINI_API_KEY),
        "models": {"loader": config.GEMINI_MODEL_LOADER, "extractor": config.GEMINI_MODEL_EXTRACTOR,
                   "generator": config.GEMINI_MODEL_GENERATOR, "embedding": config.GEMINI_MODEL_EMBEDDING},
        "db": config.DB_PATH.name,
        "input_dir": str(config.INPUT_DIR),
        "output_dir": str(config.OUTPUT_DIR),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "qdrant": {"host": config.QDRANT_HOST, "port": config.QDRANT_PORT, "reachable": _qdrant_reachable()},
        "busy": _CURRENT["job"].view() if _CURRENT["job"] else None,
    }
    try:
        with repo():
            info["db_ok"] = True
    except Exception as e:
        info["db_ok"], info["db_error"] = False, str(e)
    return info


def _qdrant_reachable() -> bool:
    host, port = config.QDRANT_HOST, config.QDRANT_PORT
    if "://" in host:
        u = urlparse(host)
        host, port = u.hostname or host, u.port or port
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except OSError:
        return False


def list_projects() -> list[dict[str, Any]]:
    with repo() as r:
        out = []
        for p in r.list_projects():
            reqs = r.list_requirements(project_id=p["id"])
            out.append({"id": p["id"], "name": p["name"], "description": p.get("description"),
                        "documents": len(p["document_ids"]), "requirements": _count_status(reqs)})
        return out


def project_detail(project_id: str) -> dict[str, Any]:
    with repo() as r:
        p = r.get_project(project_id)
        if p is None:
            raise LookupError(f"Project {project_id} not found")
        docs = []
        for d in r.list_project_documents(project_id):
            docs.append({"id": d["id"], "filename": d["filename"], "passages": len(r.list_passages(d["id"]))})
        reqs = r.list_requirements(project_id=project_id)
    st = project_state(project_id)
    json_path = config.OUTPUT_DIR / f"srs_{project_id}.json"
    return {
        "id": p["id"], "name": p["name"], "description": p.get("description"), "documents": docs,
        "requirements": _count_status(reqs),
        "has_drafts": bool(st.get("drafts")),
        "has_srs": json_path.is_file(),
    }


def _count_status(reqs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for q in reqs:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    return counts


def list_requirements(project_id: str) -> list[dict[str, Any]]:
    from services import evidence_validator

    with repo() as r:
        rows = r.list_requirements(project_id=project_id)
        ids = [e["passage_id"] for row in rows for e in row.get("evidence") or []]
        passages = {p["id"]: p for p in r.get_passages(list(dict.fromkeys(ids)))} if ids else {}
        out = []
        for row in rows:
            out.append({
                **{k: row.get(k) for k in ("id", "type", "statement", "priority", "status", "feature",
                                          "stimulus_or_condition", "response", "nfr_category",
                                          "verification_method", "acceptance_measure")},
                "problems": evidence_validator.evidence_problems(row, r),
                "evidence": [{
                    "passage_id": e["passage_id"], "quote": e.get("quote"),
                    "page": (passages.get(e["passage_id"]) or {}).get("page"),
                    "passage_text": (passages.get(e["passage_id"]) or {}).get("text"),
                } for e in row.get("evidence") or []],
            })
        return out


def safe_upload_name(name: str) -> str:
    name = Path(unquote(name or "")).name
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name or "upload"


def store_upload(name: str, data: bytes) -> Path:
    """Save into INPUT_DIR; same name + same bytes reuses the file, otherwise a suffix is added."""
    config.INPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = config.INPUT_DIR / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        if target.read_bytes() == data:
            return target
        target = config.INPUT_DIR / f"{stem}-{n}{suffix}"
        n += 1
    target.write_bytes(data)
    return target


# ---------------------------------------------------------------- HTTP


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "SpecTestAI-demo"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console readable
        pass

    # ---- plumbing

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(to_jsonable(data), ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(400, f"Invalid JSON body: {e}")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET" and (not parts or parts[0] == "static"):
                return self._static(parts[1:] if parts else ["index.html"])
            if not parts or parts[0] != "api":
                raise ApiError(404, "Not found")
            self._api(method, parts[1:], query)
        except ApiError as e:
            self._json({"error": str(e)}, e.status)
        except BusyError as e:
            self._json({"error": str(e)}, 409)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except Exception as e:
            logger.exception("request failed")
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _static(self, parts: list[str]) -> None:
        path = (STATIC_DIR / "/".join(parts or ["index.html"])).resolve()
        if STATIC_DIR not in path.parents or not path.is_file():
            raise ApiError(404, "Not found")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # ---- API

    def _api(self, method: str, p: list[str], q: dict[str, str]) -> None:
        route = (method, *p[:1])

        if route == ("GET", "status"):
            return self._json(status_info())

        if route == ("GET", "jobs") and len(p) == 2:
            job = JOBS.get(p[1])
            if job is None:
                raise ApiError(404, "Job not found")
            return self._json(job.view(int(q.get("since", 0))))

        if route == ("GET", "download"):
            name = Path(q.get("file", "")).name
            path = config.OUTPUT_DIR / name
            if not name.endswith((".md", ".json")) or not path.is_file():
                raise ApiError(404, "File not found")
            ctype = "text/markdown; charset=utf-8" if name.endswith(".md") else "application/json; charset=utf-8"
            ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
            return self._send(200, path.read_bytes(), ctype, {
                "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"})

        if route == ("GET", "projects") and len(p) == 1:
            return self._json(list_projects())

        if route == ("POST", "projects") and len(p) == 1:
            from services import project_registry

            body = self._body_json()
            pid = project_registry.create(body.get("name", ""), description=body.get("description") or None)
            return self._json(project_detail(pid), 201)

        if p[:1] == ["projects"] and len(p) >= 2:
            return self._project_api(method, p[1], p[2:], q)

        if route == ("POST", "requirements") and len(p) == 3 and p[2] in ("approve", "reject"):
            from services import requirement_registry

            try:
                if p[2] == "approve":
                    requirement_registry.approve(p[1])
                else:
                    requirement_registry.reject(p[1])
            except requirement_registry.RegistryError as e:
                raise ApiError(409, str(e))
            return self._json({"ok": True, "id": p[1], "status": p[2] + "d"})

        raise ApiError(404, f"Unknown endpoint {method} /api/{'/'.join(p)}")

    def _project_api(self, method: str, pid: str, rest: list[str], q: dict[str, str]) -> None:
        action = rest[0] if rest else ""

        if method == "GET" and not rest:
            return self._json(project_detail(pid))
        if method == "GET" and action == "requirements":
            return self._json(list_requirements(pid))

        if method != "POST":
            raise ApiError(404, "Not found")
        project_detail(pid)  # 404 if missing

        if action == "upload":
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ApiError(400, "Empty upload")
            if length > MAX_UPLOAD_BYTES:
                raise ApiError(413, f"File is {length / 1048576:.1f} MB; the limit is "
                                    f"{MAX_UPLOAD_BYTES // 1048576} MB")
            name = safe_upload_name(q.get("filename", ""))
            if not name.lower().endswith(ALLOWED_EXTENSIONS):
                raise ApiError(415, f"Unsupported file type; allowed: {', '.join(ALLOWED_EXTENSIONS)}")
            data = self.rfile.read(length)
            if _CURRENT["job"] is not None:
                raise BusyError("Another step is still running; wait for it")
            path = store_upload(name, data)
            job = start_job("ingest", pid, step_ingest(pid, path))
            return self._json({"job": job.id, "stored_as": path.name}, 202)

        if action == "index":
            return self._json({"job": start_job("index", pid, step_index(pid)).id}, 202)
        if action == "extract":
            ids = self._body_json().get("document_ids") or []
            return self._json({"job": start_job("extract", pid, step_extract(pid, ids)).id}, 202)
        if action == "validate":
            return self._json({"job": start_job("validate", pid, step_validate_save(pid)).id}, 202)
        if action == "approve-all":
            return self._json(self._approve_all(pid))
        if action == "generate":
            body = self._body_json()
            authors = [a.strip() for a in str(body.get("authors") or "").split(",") if a.strip()]
            job = start_job("generate", pid, step_generate(pid, (body.get("organization") or "").strip(),
                                                           authors, (body.get("version") or "1.0").strip()))
            return self._json({"job": job.id}, 202)
        if action == "export":
            return self._json({"job": start_job("export", pid, step_export(pid)).id}, 202)
        raise ApiError(404, f"Unknown project action {action!r}")

    def _approve_all(self, pid: str) -> dict[str, Any]:
        from services import requirement_registry

        approved, failed = [], []
        for row in requirement_registry.list_by_status("pending", project_id=pid):
            try:
                requirement_registry.approve(row["id"])
                approved.append(row["id"])
            except requirement_registry.RegistryError as e:
                failed.append({"id": row["id"], "error": str(e)})
        return {"approved": approved, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m frontend.server", description="SpecTestAI demo UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    args = parser.parse_args(argv)

    app_logging.setup_cli_logging("frontend", quiet_http=True)
    install_log_capture()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    logger.info("SpecTestAI demo UI on %s (Ctrl+C to stop)", url)
    if args.open:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
