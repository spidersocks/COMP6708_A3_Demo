"""Markdown load + chunk helpers for BrightNest KB."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class DocumentChunk:
    id: str
    text: str
    metadata: Dict[str, Any]


def parse_markdown(path: Path) -> Tuple[Dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    m = FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, (m.group(2) or "").strip()


def _split_paragraphs(text: str) -> List[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(
    text: str,
    *,
    chunk_size: int = 900,
    overlap: int = 120,
) -> List[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    paragraphs = _split_paragraphs(text)
    chunks: List[str] = []
    buf = ""
    for para in paragraphs:
        if not buf:
            buf = para
            continue
        if len(buf) + 2 + len(para) <= chunk_size:
            buf = f"{buf}\n\n{para}"
        else:
            chunks.append(buf)
            # overlap by trailing chars
            tail = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = f"{tail}\n\n{para}".strip() if tail else para
    if buf:
        chunks.append(buf)

    # Hard-split any oversize chunk
    final: List[str] = []
    for c in chunks:
        if len(c) <= chunk_size * 1.5:
            final.append(c)
        else:
            step = max(chunk_size - overlap, 200)
            for i in range(0, len(c), step):
                final.append(c[i : i + chunk_size])
    return final


def load_corpus(content_dir: Path) -> List[Tuple[Path, Dict[str, Any], str]]:
    docs: List[Tuple[Path, Dict[str, Any], str]] = []
    if not content_dir.exists():
        return docs
    for path in sorted(content_dir.rglob("*.md")):
        meta, body = parse_markdown(path)
        if not body:
            continue
        rel = str(path.relative_to(content_dir)).replace("\\", "/")
        meta = dict(meta)
        meta.setdefault("source", rel)
        meta.setdefault("language", path.parts[-3] if len(path.parts) >= 3 else "en")
        docs.append((path, meta, body))
    return docs


def build_chunks(
    content_dir: Path,
    *,
    chunk_size: int = 900,
    overlap: int = 120,
    language_filter: Optional[str] = None,
) -> List[DocumentChunk]:
    out: List[DocumentChunk] = []
    for path, meta, body in load_corpus(content_dir):
        lang = str(meta.get("language") or "en")
        if language_filter and lang != language_filter:
            continue
        title = ""
        for line in body.splitlines():
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                break
        pieces = chunk_text(body, chunk_size=chunk_size, overlap=overlap)
        source = meta.get("source") or path.name
        for i, piece in enumerate(pieces):
            header = f"[{title}]\n" if title else ""
            text = f"{header}{piece}".strip()
            cid = f"{source}:::{i}"
            md = {
                "source": source,
                "language": lang,
                "canonical": str(meta.get("canonical") or ""),
                "type": str(meta.get("type") or ""),
                "folder": str(meta.get("folder") or ""),
                "title": title,
                "chunk_index": i,
            }
            out.append(DocumentChunk(id=cid, text=text, metadata=md))
    return out
