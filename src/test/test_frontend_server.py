"""frontend.server: every pipeline step through the HTTP API (offline).

Run from src/:  python -m unittest test.test_frontend_server
Gemini, Qdrant and embeddings are faked; the database and the input / output
folders are temporary. Fixtures are synthetic.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import config
from agents import requirement_extractor, srs_generator
from frontend import server
from retrieval import indexer
from schemas.requirement import RequirementDraft
from services import document_loader

TEXT = "Users log in with a password.\n\nAdmins can export monthly reports."


def fake_parse(client, model, contents, name):
    return document_loader.ParsedDocument(paragraphs=[
        document_loader.ParsedParagraph(page=None, text="Users log in with a password."),
        document_loader.ParsedParagraph(page=None, text="Admins can export monthly reports."),
    ])


def fake_extract(passages, **kw):
    by_text = {p["text"]: p["id"] for p in passages}
    doc = passages[0]["document_id"]
    good = RequirementDraft.model_validate({
        "type": "functional", "statement": "The system shall let users log in.", "priority": "must",
        "feature": "Login", "evidence": [{"passage_id": by_text["Users log in with a password."],
                                          "quote": "Users log in"}]})
    bad = RequirementDraft.model_validate({
        "type": "functional", "statement": "The system shall print invoices.", "priority": "should",
        "feature": "Billing", "evidence": [{"passage_id": by_text["Users log in with a password."],
                                            "quote": "print invoices"}]})  # quote not in the passage
    return [requirement_extractor.ExtractionReport(doc, len(passages), requirements=[good, bad])]


def fake_model_draft(c, m, features, nonfunctional, passages, feedback, **kw):
    return ({
        **{name: {"status": "unresolved", "text": None, "reason": "Not stated.", "source_ids": []}
           for name in srs_generator.NARRATIVE_SECTIONS},
        "features": [{"feature": name, "description": f"{name} feature.",
                      "sequences": [{"stimulus_or_condition": "User acts", "response": "System responds",
                                     "requirement_ids": [reqs[0]["id"]]}]}
                     for name, reqs in features],
    }, None)


class FrontendServerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        server.STATE.clear()
        root = logging.getLogger()
        saved = (root.level, list(root.handlers))
        self.addCleanup(lambda: (root.setLevel(saved[0]), setattr(root, "handlers", saved[1])))
        server.install_log_capture()
        for target, attr, value in [
            (config, "DB_PATH", tmp / "spectest.db"), (config, "INPUT_DIR", tmp / "input"),
            (config, "OUTPUT_DIR", tmp / "output"), (config, "GEMINI_CONTEXT_CACHE", "off"),
            (document_loader, "_call_gemini", fake_parse),
            (requirement_extractor, "extract_with_report", fake_extract),
            (indexer, "index_passages", lambda **kw: indexer.IndexReport(collection="test", in_scope=2, embedded=2)),
            (srs_generator, "_retrieve", lambda *a: {}),
            (srs_generator, "_ask_model", fake_model_draft),
            (server, "_qdrant_reachable", lambda: False),
        ]:
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, method: str, path: str, body=None, raw: bytes | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, job_id: str) -> dict:
        for _ in range(200):
            _, view = self.call("GET", f"/api/jobs/{job_id}")
            if view["status"] != "running":
                return view
            time.sleep(0.05)
        self.fail("job did not finish")

    def run_step(self, path: str, body=None, raw=None) -> dict:
        status, started = self.call("POST", path, body, raw)
        self.assertEqual(status, 202, started)
        view = self.wait(started["job"])
        self.assertEqual(view["status"], "done", view["error"])
        self.assertTrue(view["logs"], "job logs are captured")
        return view["result"]

    def test_full_pipeline_from_upload_to_markdown(self) -> None:
        status, page = 0, None
        with urllib.request.urlopen(self.base + "/") as res:
            status, page = res.status, res.read().decode()
        self.assertEqual(status, 200)
        self.assertIn("SpecTestAI", page)

        status, prj = self.call("POST", "/api/projects", {"name": "Demo Portal"})
        self.assertEqual(status, 201)
        pid = prj["id"]

        ingest = self.run_step(f"/api/projects/{pid}/upload?filename=spec.md", raw=TEXT.encode())
        self.assertEqual(ingest["passage_count"], 2)
        self.assertTrue((config.INPUT_DIR / "spec.md").is_file())

        self.assertEqual(self.run_step(f"/api/projects/{pid}/index")["embedded"], 2)

        extract = self.run_step(f"/api/projects/{pid}/extract", {"document_ids": []})
        self.assertEqual(len(extract["requirements"]), 2)

        saved = self.run_step(f"/api/projects/{pid}/validate")
        self.assertEqual(saved["valid"], 1)
        self.assertEqual(len(saved["invalid"]), 1)
        self.assertEqual(saved["saved_ids"], ["RQ-0001"])

        _, reqs = self.call("GET", f"/api/projects/{pid}/requirements")
        self.assertEqual([(r["id"], r["status"], r["problems"]) for r in reqs], [("RQ-0001", "pending", [])])
        self.assertEqual(reqs[0]["evidence"][0]["passage_text"], "Users log in with a password.")
        status, approved = self.call("POST", f"/api/projects/{pid}/approve-all")
        self.assertEqual(approved, {"approved": ["RQ-0001"], "failed": []})

        gen = self.run_step(f"/api/projects/{pid}/generate", {"organization": "ExampleCorp", "authors": "A, B"})
        self.assertEqual(gen["requirement_map"], {"REQ-1.1": "RQ-0001"})

        out = self.run_step(f"/api/projects/{pid}/export")
        self.assertTrue(out["ok"], out.get("errors"))
        self.assertIn("The system shall let users log in.", out["markdown"])
        self.assertTrue((config.OUTPUT_DIR / out["file"]).is_file())

        req = urllib.request.Request(f"{self.base}/api/download?file={out['file']}")
        with urllib.request.urlopen(req) as res:
            self.assertIn("attachment", res.headers["Content-Disposition"])

    def test_upload_limits(self) -> None:
        _, prj = self.call("POST", "/api/projects", {"name": "Limits"})
        pid = prj["id"]
        status, body = self.call("POST", f"/api/projects/{pid}/upload?filename=a.exe", raw=b"x")
        self.assertEqual(status, 415, body)
        with mock.patch.object(server, "MAX_UPLOAD_BYTES", 4):
            status, body = self.call("POST", f"/api/projects/{pid}/upload?filename=a.txt", raw=b"12345")
        self.assertEqual(status, 413, body)
        status, _ = self.call("GET", "/api/download?file=../config.py")
        self.assertEqual(status, 404)
        self.assertEqual(server.safe_upload_name("..\\..\\evil:name?.txt"), "evil_name_.txt")


if __name__ == "__main__":
    unittest.main()
