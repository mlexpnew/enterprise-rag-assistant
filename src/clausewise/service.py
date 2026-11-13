"""Build the desk from environment settings (used by the API, the dashboard and the CLI)."""

from __future__ import annotations

import os
from pathlib import Path

from .corpus import load_dir
from .desk import Desk

SAMPLE = Path(__file__).parent / "sample_policies"


def build(policy_dir: str | None = None) -> Desk:
    folder = policy_dir or os.getenv("CLAUSEWISE_POLICY_DIR") or SAMPLE
    rephrase = None
    if os.getenv("CLAUSEWISE_LLM"):
        from .llm import rephraser

        rephrase = rephraser(os.environ["CLAUSEWISE_LLM"])
    embed = None
    if os.getenv("CLAUSEWISE_SEMANTIC"):
        from .semantic import sentence_transformer

        embed = sentence_transformer()
    threshold = float(os.getenv("CLAUSEWISE_SEM_THRESHOLD", "0.5"))
    return Desk(load_dir(folder), rephrase=rephrase, embed=embed, sem_threshold=threshold)
