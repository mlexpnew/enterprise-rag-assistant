"""Optional meaning-based search with sentence embeddings.

Keyword search misses questions worded unlike the policy ("small town" for non-metro).
Embeddings close that gap. Install with `pip install "clausewise[semantic]"`; the same
safety gates (role, version, refusal threshold, verbatim citation) apply either way.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

Embed = Callable[[list[str]], np.ndarray]
MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def sentence_transformer(model: str = MODEL) -> Embed:
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(model)
    return lambda texts: np.asarray(m.encode(texts, normalize_embeddings=True))


class Semantic:
    def __init__(self, texts: list[str], embed: Embed):
        self.embed = embed
        self.m = embed(texts)

    def scores(self, question: str) -> np.ndarray:
        q = self.embed([question])[0]
        return self.m @ q
