"""The single definition of "valid evidence", shared by every layer.

Used by:
  - database.sqlite_repository  -> a requirement can only be stored as / set to
                                   "approved" when its evidence is valid
  - services.evidence_validator -> filters extractor candidates before saving
  - services.document_loader    -> normalize_for_match() verifies passages

Valid evidence: at least one item, and EVERY item
  - points to an existing passage,
  - carries a quote (missing or blank quotes cannot be verified),
  - whose text is found inside that same passage after normalize_for_match().
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


def normalize_for_match(text: str) -> str:
    """Letters and digits only, case-folded: ignores whitespace, punctuation,
    bullets, and hyphenation differences between the model and the source."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def evidence_problems(
    evidence: Iterable[Mapping[str, Any]], passage_texts: Mapping[str, str]
) -> list[str]:
    """Problems with a requirement's evidence; an empty list means valid.

    `evidence` items are mappings with "passage_id" and "quote";
    `passage_texts` maps passage id -> passage text for the cited passages
    (a cited id missing from it counts as "passage not found").
    """
    items = list(evidence)
    if not items:
        return ["no evidence"]
    problems = []
    for item in items:
        pid, quote = item.get("passage_id"), item.get("quote")
        if pid not in passage_texts:
            problems.append(f"{pid}: passage not found")
        elif not quote or not normalize_for_match(quote):
            problems.append(f"{pid}: no quote to verify")
        elif normalize_for_match(quote) not in normalize_for_match(passage_texts[pid]):
            problems.append(f"{pid}: quote not found in passage: {quote[:80]!r}")
    return problems
