"""Core context: the fixed knowledge every SRS-related model call starts from.

Four sources, always in this order:

    key           file                                    role
    srs_config    templates/standard_srs.yaml             document control + template configuration
    srs_template  templates/IEEE_830_SRS_Template.md      section structure, order, writing guidance
    glossary      knowledge/core/company_glossary.md      canonical internal terminology
    guidelines    knowledge/core/requirement_guidelines.md FR/NFR, MoSCoW, evidence, unresolved rules

The same files give the same context and the same version, on any machine:

- Text is normalised before use: UTF-8 (BOM removed), CRLF / CR -> LF,
  exactly one trailing newline. A git checkout with autocrlf therefore does
  not change the version.
- source sha256 = sha256 of the normalised text.
- version = "core-" + first 16 hex of
  sha256(FORMAT_VERSION, then key + source sha256 for each source in order).
  It changes when any source's content changes, when sources are added,
  removed or reordered, or when FORMAT_VERSION is bumped (bump it whenever
  render() changes, since the rendered text is what the model sees).

The version identifies the context in logs, SRS runs, and caches (e.g. a
Gemini context cache keyed by it: same version -> reuse, new version ->
rebuild).

    python -m context.core_context            # manifest: version + per-source hash
    python -m context.core_context --render   # the full text sent to the model
    python -m context.core_context --json     # manifest as JSON
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config

FORMAT_VERSION = "core-context/1"


@dataclass(frozen=True)
class SourceSpec:
    key: str
    path: str      # relative to the base directory (src/)
    title: str
    role: str


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("srs_config", "templates/standard_srs.yaml", "SRS template configuration",
               "Document control and template configuration; keys match the SRS schema."),
    SourceSpec("srs_template", "templates/IEEE_830_SRS_Template.md", "IEEE 830 SRS template",
               "Section structure, order, and guidance for writing each section."),
    SourceSpec("glossary", "knowledge/core/company_glossary.md", "Company glossary",
               "Canonical internal terminology for requirement statements and SRS text."),
    SourceSpec("guidelines", "knowledge/core/requirement_guidelines.md", "Requirement guidelines",
               "Rules for FR/NFR, MoSCoW priority, evidence, and unresolved sections."),
)


class CoreContextError(Exception):
    """A core source is missing, empty, or not valid UTF-8."""


@dataclass(frozen=True)
class CoreSource:
    key: str
    title: str
    role: str
    path: str      # relative, forward slashes
    text: str      # normalised
    sha256: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class CoreContext:
    version: str
    sources: tuple[CoreSource, ...]

    def source(self, key: str) -> CoreSource:
        for s in self.sources:
            if s.key == key:
                return s
        raise KeyError(key)

    def render(self) -> str:
        """The text given to the model. Deterministic for a given version."""
        parts = [f'<core_context version="{self.version}">']
        for s in self.sources:
            parts.append(
                f'<source key="{s.key}" title="{s.title}" path="{s.path}" sha256="{s.sha256[:16]}">\n'
                f"<!-- {s.role} -->\n{s.text}</source>"
            )
        parts.append("</core_context>")
        return "\n".join(parts) + "\n"

    def manifest(self) -> dict[str, Any]:
        """Version and per-source hashes, e.g. to store with an SRS run."""
        return {
            "version": self.version,
            "format": FORMAT_VERSION,
            "sources": [
                {"key": s.key, "path": s.path, "sha256": s.sha256, "chars": s.chars}
                for s in self.sources
            ],
        }

    def template_data(self) -> dict[str, Any]:
        """standard_srs.yaml parsed (needs PyYAML), e.g. for srs_generator(template=...)."""
        try:
            import yaml
        except ImportError as e:
            raise CoreContextError("Parsing standard_srs.yaml needs PyYAML (pip install pyyaml)") from e
        data = yaml.safe_load(self.source("srs_config").text)
        if not isinstance(data, dict):
            raise CoreContextError("templates/standard_srs.yaml is not a YAML mapping")
        return data


# ----------------------------------------------------------------- loading


def normalize_text(raw: bytes, path: str = "<bytes>") -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CoreContextError(f"{path}: not valid UTF-8 ({e})") from e
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n") + "\n"


def compute_version(sources: Sequence[CoreSource]) -> str:
    h = hashlib.sha256(FORMAT_VERSION.encode("utf-8"))
    for s in sources:
        h.update(b"\0" + s.key.encode("utf-8") + b"\0" + s.sha256.encode("ascii"))
    return "core-" + h.hexdigest()[:16]


def build_core_context(
    base_dir: str | Path | None = None,
    specs: Sequence[SourceSpec] = SOURCES,
) -> CoreContext:
    """Read and hash every source now (no cache)."""
    base = Path(base_dir) if base_dir is not None else config.SRC_DIR
    sources = []
    for spec in specs:
        path = base / spec.path
        try:
            raw = path.read_bytes()
        except FileNotFoundError as e:
            raise CoreContextError(f"{spec.path}: core source {spec.key!r} not found") from e
        text = normalize_text(raw, spec.path)
        if not text.strip():
            raise CoreContextError(f"{spec.path}: core source {spec.key!r} is empty")
        sources.append(CoreSource(
            key=spec.key, title=spec.title, role=spec.role, path=Path(spec.path).as_posix(),
            text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ))
    return CoreContext(version=compute_version(sources), sources=tuple(sources))


_cache: dict[tuple[Any, ...], CoreContext] = {}


def load_core_context(
    base_dir: str | Path | None = None,
    specs: Sequence[SourceSpec] = SOURCES,
) -> CoreContext:
    """Cached build_core_context(): files are re-read only when one of them
    changes (size or modification time), so callers may call it per request."""
    base = Path(base_dir) if base_dir is not None else config.SRC_DIR
    key = (str(base.resolve()), tuple(spec for spec in specs), _file_signature(base, specs))
    ctx = _cache.get(key)
    if ctx is None:
        ctx = build_core_context(base, specs)
        _cache.clear()  # only the current state is worth keeping
        _cache[key] = ctx
    return ctx


def _file_signature(base: Path, specs: Sequence[SourceSpec]) -> tuple[Any, ...]:
    sig = []
    for spec in specs:
        try:
            st = (base / spec.path).stat()
            sig.append((spec.path, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            sig.append((spec.path, None, None))
    return tuple(sig)


# --------------------------------------------------------------------- CLI


def _print_manifest(ctx: CoreContext) -> None:
    print(f"core context {ctx.version}  ({FORMAT_VERSION})")
    for s in ctx.sources:
        print(f"  {s.key:<13} {s.sha256[:16]}  {s.chars:>6} chars  {s.path}")
    print(f"  rendered: {len(ctx.render())} chars")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    import argparse

    parser = argparse.ArgumentParser(prog="python -m context.core_context",
                                     description="Load the core context and show its version.")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--render", action="store_true", help="print the full rendered context")
    out.add_argument("--json", action="store_true", help="print the manifest as JSON")
    args = parser.parse_args()
    try:
        context = load_core_context()
    except CoreContextError as e:
        print(f"ERROR {e}", file=sys.stderr)
        sys.exit(1)
    if args.render:
        sys.stdout.write(context.render())
    elif args.json:
        print(json.dumps(context.manifest(), indent=2))
    else:
        _print_manifest(context)
