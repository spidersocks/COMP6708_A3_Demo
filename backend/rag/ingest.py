"""Ingest markdown KB into Chroma."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python -m rag.ingest` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SETTINGS
from rag.chunking import build_chunks
from rag.embeddings import embed_texts
from rag.store import get_store


def ingest(*, reset: bool = True) -> dict:
    chunks = build_chunks(
        SETTINGS.content_dir,
        chunk_size=SETTINGS.rag_chunk_size,
        overlap=SETTINGS.rag_chunk_overlap,
    )
    store = get_store()
    if reset:
        store.reset()
    if not chunks:
        return {"chunks": 0, "mode": "empty", "content_dir": str(SETTINGS.content_dir)}

    print(f"[INGEST] Embedding {len(chunks)} chunks…", flush=True)
    embeddings = embed_texts([c.text for c in chunks])
    n = store.upsert_chunks(chunks, embeddings)
    mode = "dashscope" if SETTINGS.has_dashscope else "local_hash"
    print(f"[INGEST] Upserted {n} chunks ({mode}).", flush=True)
    return {
        "chunks": n,
        "mode": mode,
        "content_dir": str(SETTINGS.content_dir),
        "collection": SETTINGS.collection_name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest BrightNest KB into Chroma")
    parser.add_argument("--no-reset", action="store_true", help="Append without clearing collection")
    args = parser.parse_args()
    result = ingest(reset=not args.no_reset)
    print(result)


if __name__ == "__main__":
    main()
