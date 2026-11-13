"""Optional LLM to phrase an answer in plain words. The quoted clause stays the source of
truth: a phrasing that uses any number not in the clause is discarded (see desk.grounded)."""

from __future__ import annotations

import os
from collections.abc import Callable

PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "gemini-2.5-flash",
    ),
    "ollama": ("http://localhost:11434/v1", None, "qwen2.5:7b-instruct"),
}

PROMPT = """Answer the employee's question in one or two plain sentences using ONLY the policy
text below. Do not add any number, date, amount or condition that is not in it.
Question: {q}
Policy text: {src}"""


def rephraser(
    provider: str, post: Callable | None = None
) -> Callable[[str, list[str]], str | None]:
    import requests

    base, key_env, model = PRESETS[provider]
    key = os.getenv(key_env) if key_env else None
    if key_env and not key:
        raise RuntimeError(f"{key_env} is not set")
    headers = {
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {key}"} if key else {}),
    }

    def _post(url, body):
        r = requests.post(url, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    send = post or _post

    def phrase(q: str, sources: list[str]) -> str | None:
        body = {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": PROMPT.format(q=q, src=" ".join(sources))}],
        }
        try:
            return send(f"{base}/chat/completions", body)["choices"][0]["message"][
                "content"
            ].strip()
        except Exception:
            return None

    return phrase
