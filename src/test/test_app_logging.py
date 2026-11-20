"""app_logging: console + daily log file for the CLIs (offline)."""

import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_logging


class SetupCliLoggingTest(unittest.TestCase):
    def setUp(self) -> None:
        root = logging.getLogger()
        self.saved = (root.level, list(root.handlers), logging.getLogger("httpx").level)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "logs"
        stderr = mock.patch("sys.stderr", io.StringIO())  # the console handler binds it at setup
        stderr.start()
        self.addCleanup(stderr.stop)

    def tearDown(self) -> None:
        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, app_logging._MARK, False):
                root.removeHandler(h)
                h.close()
        level, handlers, httpx_level = self.saved
        root.setLevel(level)
        root.handlers[:] = handlers
        logging.getLogger("httpx").setLevel(httpx_level)
        self.tmp.cleanup()

    def read(self, path: Path) -> str:
        for h in logging.getLogger().handlers:
            h.flush()
        return path.read_text(encoding="utf-8")

    def test_writes_a_daily_file_with_tool_and_logger_name(self) -> None:
        path = app_logging.setup_cli_logging("srs_generator", log_dir=self.dir, file_level="INFO")
        self.assertEqual(path, app_logging.log_file_path(self.dir))
        self.assertRegex(path.name, r"^spectest-\d{4}-\d{2}-\d{2}\.log$")
        logging.getLogger("retrieval.retriever").info("retrieval hybrid: hits=%d", 3)
        text = self.read(path)
        self.assertIn("start: ", text)
        self.assertRegex(text, r"INFO \[srs_generator \d+\] retrieval\.retriever: retrieval hybrid: hits=3")

    def test_http_request_lines_stay_out_of_the_file(self) -> None:
        path = app_logging.setup_cli_logging("indexer", log_dir=self.dir, file_level="INFO")
        logging.getLogger("httpx").info("HTTP Request: POST https://example.invalid")
        logging.getLogger("httpx").warning("connection reset")
        text = self.read(path)
        self.assertNotIn("HTTP Request", text)
        self.assertIn("connection reset", text)

    def test_file_level_debug_keeps_debug_out_of_the_console(self) -> None:
        path = app_logging.setup_cli_logging("srs_generator", log_dir=self.dir, file_level="DEBUG")
        logging.getLogger("agents.srs_generator").debug("retrieval scope -> P1")
        self.assertIn("retrieval scope -> P1", self.read(path))
        consoles = [h for h in logging.getLogger().handlers
                    if getattr(h, app_logging._MARK, False) and not isinstance(h, logging.FileHandler)]
        self.assertEqual([h.level for h in consoles], [logging.INFO])

    def test_appends_across_runs_and_does_not_duplicate_handlers(self) -> None:
        app_logging.setup_cli_logging("a", log_dir=self.dir, file_level="INFO")
        logging.getLogger("x").info("first")
        path = app_logging.setup_cli_logging("b", log_dir=self.dir, file_level="INFO")
        logging.getLogger("x").info("second")
        text = self.read(path)
        self.assertEqual((text.count("first"), text.count("second")), (1, 1))
        self.assertEqual(sum(getattr(h, app_logging._MARK, False) for h in logging.getLogger().handlers), 2)

    def test_disabled_writes_no_file(self) -> None:
        self.assertIsNone(app_logging.setup_cli_logging("x", log_dir=None))
        logging.getLogger("x").info("hello")
        self.assertFalse(self.dir.exists())

    def test_unwritable_directory_only_warns(self) -> None:
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("not a directory")
        with self.assertLogs("app_logging", "WARNING") as logs:
            self.assertIsNone(app_logging.setup_cli_logging("x", log_dir=blocker / "logs"))
        self.assertIn("log file disabled", logs.output[0])


if __name__ == "__main__":
    unittest.main()
