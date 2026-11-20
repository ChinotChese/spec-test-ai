"""context.core_context: load the four core sources, version hash.

Run from src/:  python -m unittest discover -s test -t .
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import config
from context import core_context as cc


class RealSourcesTest(unittest.TestCase):
    def test_loads_the_four_sources_in_order(self) -> None:
        ctx = cc.build_core_context()
        self.assertEqual([s.key for s in ctx.sources],
                         ["srs_config", "srs_template", "glossary", "guidelines"])
        for s in ctx.sources:
            self.assertTrue(s.text.strip(), s.path)
        self.assertRegex(ctx.version, r"^core-[0-9a-f]{16}$")

    def test_render_contains_every_source_and_the_version(self) -> None:
        ctx = cc.build_core_context()
        text = ctx.render()
        self.assertIn(f'<core_context version="{ctx.version}">', text)
        for s in ctx.sources:
            self.assertIn(f'key="{s.key}"', text)
            self.assertIn(s.text, text)

    def test_manifest_lists_full_hashes(self) -> None:
        manifest = cc.build_core_context().manifest()
        self.assertEqual(manifest["format"], cc.FORMAT_VERSION)
        self.assertEqual(len(manifest["sources"]), 4)
        self.assertTrue(all(len(s["sha256"]) == 64 for s in manifest["sources"]))


class VersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        for spec in cc.SOURCES:
            target = self.base / spec.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(config.SRC_DIR / spec.path, target)

    def write(self, key: str, data: bytes) -> None:
        spec = next(s for s in cc.SOURCES if s.key == key)
        (self.base / spec.path).write_bytes(data)

    def version(self) -> str:
        return cc.build_core_context(self.base).version

    def test_same_files_same_version(self) -> None:
        self.assertEqual(self.version(), self.version())
        self.assertEqual(self.version(), cc.build_core_context().version)

    def test_content_change_changes_version(self) -> None:
        before = self.version()
        self.write("glossary", b"# Glossary\n\n| Use | Avoid | Meaning |\n|---|---|---|\n| x | y | z |\n")
        after = self.version()
        self.assertNotEqual(before, after)
        changed = [s.key for s, o in zip(cc.build_core_context(self.base).sources,
                                         cc.build_core_context().sources) if s.sha256 != o.sha256]
        self.assertEqual(changed, ["glossary"])

    def test_line_endings_bom_and_trailing_newlines_do_not_change_version(self) -> None:
        self.write("guidelines", b"# Rules\nOne\nTwo\n")
        base = self.version()
        for variant in (b"# Rules\r\nOne\r\nTwo\r\n", b"\xef\xbb\xbf# Rules\nOne\nTwo\n",
                        b"# Rules\nOne\nTwo", b"# Rules\nOne\nTwo\n\n\n"):
            with self.subTest(variant=variant):
                self.write("guidelines", variant)
                self.assertEqual(self.version(), base)

    def test_order_and_format_are_part_of_the_version(self) -> None:
        normal = self.version()
        reordered = cc.build_core_context(self.base, tuple(reversed(cc.SOURCES))).version
        self.assertNotEqual(normal, reordered)
        original = cc.FORMAT_VERSION
        try:
            cc.FORMAT_VERSION = "core-context/test"
            self.assertNotEqual(self.version(), normal)
        finally:
            cc.FORMAT_VERSION = original

    def test_missing_empty_and_non_utf8_sources_are_refused(self) -> None:
        self.write("glossary", b"  \n\n")
        with self.assertRaisesRegex(cc.CoreContextError, "glossary.*empty"):
            self.version()
        self.write("glossary", b"\xff\xfe bad")
        with self.assertRaisesRegex(cc.CoreContextError, "UTF-8"):
            self.version()
        (self.base / "knowledge/core/company_glossary.md").unlink()
        with self.assertRaisesRegex(cc.CoreContextError, "not found"):
            self.version()

    def test_cache_reloads_when_a_file_changes(self) -> None:
        first = cc.load_core_context(self.base)
        self.assertIs(cc.load_core_context(self.base), first)
        self.write("guidelines", b"# Changed rules, different size\n")
        second = cc.load_core_context(self.base)
        self.assertNotEqual(second.version, first.version)
        self.assertEqual(second.source("guidelines").text, "# Changed rules, different size\n")


if __name__ == "__main__":
    unittest.main()
