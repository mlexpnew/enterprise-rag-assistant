"""Load policy documents (Markdown, text, PDF, Word) into numbered clauses with metadata.

Metadata comes from YAML front matter in Markdown, or from a `policies.yaml` manifest for
PDF and Word files:  {"travel.pdf": {"policy_id": "travel", "version": "3.0",
"effective": "2025-07-01", "audience": ["all"]}}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

CLAUSE = re.compile(r"^\s*(\d+(?:\.\d+)+)\s+(.*)")
HEADING = re.compile(r"^\s*#{1,6}\s*(?:(\d+)[.)]?\s+)?(.*)")


@dataclass
class Doc:
    doc_id: str  # file stem
    title: str
    policy_id: str  # versions of one policy share it
    version: str
    effective: date
    audience: list[str]
    source: str
    clauses: list[Clause] = field(default_factory=list)

    def visible_to(self, role: str) -> bool:
        return "all" in self.audience or role in self.audience or role == "hr"


@dataclass
class Clause:
    doc_id: str
    clause_id: str  # "2.1"
    section: str  # "Leave entitlement"
    text: str

    @property
    def key(self) -> str:
        return f"{self.doc_id}#{self.clause_id}"


def _front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            return yaml.safe_load(text[3:end]) or {}, text[end + 4 :]
    return {}, text


def _read(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    if suf == ".docx":
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8")


def parse(text: str, doc_id: str) -> tuple[str, list[Clause]]:
    """Split into clauses on numbering like 2.1; paragraphs become clauses if unnumbered."""
    title, section, clauses, cur = doc_id, "", [], None
    for line in text.splitlines():
        if not line.strip():
            cur = None
            continue
        h = HEADING.match(line)
        if line.lstrip().startswith("#") and h:
            if line.lstrip().startswith("# ") and title == doc_id:
                title = h.group(2).strip()
            else:
                section = h.group(2).strip()
            cur = None
            continue
        m = CLAUSE.match(line)
        if m:
            cur = Clause(doc_id, m.group(1), section, m.group(2).strip())
            clauses.append(cur)
        elif cur is not None:
            cur.text += " " + line.strip()
        else:  # unnumbered paragraph
            cur = Clause(doc_id, f"p{len(clauses) + 1}", section, line.strip())
            clauses.append(cur)
    return title, clauses


def load_dir(folder: str | Path) -> list[Doc]:
    folder = Path(folder)
    manifest_path = folder / "policies.yaml"
    manifest = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else {}
    docs = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".md", ".txt", ".pdf", ".docx"):
            continue
        meta, body = _front_matter(_read(p))
        meta = {**(manifest or {}).get(p.name, {}), **meta}
        title, clauses = parse(body, p.stem)
        eff = meta.get("effective", "1970-01-01")
        docs.append(
            Doc(
                doc_id=p.stem,
                title=meta.get("title", title),
                policy_id=meta.get("policy_id", p.stem),
                version=str(meta.get("version", "1")),
                effective=eff if isinstance(eff, date) else date.fromisoformat(str(eff)),
                audience=list(meta.get("audience", ["all"])),
                source=p.name,
                clauses=clauses,
            )
        )
    return docs
