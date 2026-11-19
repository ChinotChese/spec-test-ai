"""BM25 sparse vectors for the keyword half of hybrid search.

Computed here, not by Qdrant's server-side inference, so it works on any
Qdrant >= 1.10 and in the local mode used by tests. Qdrant applies the IDF
part itself (sparse vector "bm25" is created with Modifier.IDF); this module
supplies the term-frequency part:

    document: weight(t) = tf * (k1 + 1) / (tf + k1 * (1 - b + b * len / AVG_LEN))
    query:    weight(t) = 1 for each distinct term

Tokens: Unicode words (\\w+), lowercased, NFC-normalised. No stemming and no
stopwords, so Vietnamese syllables and English words are both kept as written
(diacritics matter: "ma" and "mã" are different terms). Term ids are a stable
32-bit hash of the token.

Changing anything here that alters the vectors must bump VERSION: the indexer
then rewrites every point's sparse vector (dense vectors are reused, so no
Gemini calls).
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter

VERSION = "bm25-v1"

K1 = 1.2
B = 0.75
AVG_LEN = 64.0  # typical passage length in tokens; only shifts the length normalisation

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


def _term_id(token: str) -> int:
    return zlib.crc32(token.encode("utf-8"))


def _to_sparse(weights: dict[int, float]) -> tuple[list[int], list[float]]:
    indices = sorted(weights)
    return indices, [weights[i] for i in indices]


def encode_document(text: str) -> tuple[list[int], list[float]]:
    tokens = tokenize(text)
    if not tokens:
        return [], []
    norm = K1 * (1 - B + B * len(tokens) / AVG_LEN)
    weights: dict[int, float] = {}
    for token, tf in Counter(tokens).items():
        term = _term_id(token)
        weights[term] = weights.get(term, 0.0) + tf * (K1 + 1) / (tf + norm)
    return _to_sparse(weights)


def encode_query(text: str) -> tuple[list[int], list[float]]:
    return _to_sparse({_term_id(t): 1.0 for t in set(tokenize(text))})
