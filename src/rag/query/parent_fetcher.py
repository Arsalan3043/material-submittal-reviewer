from __future__ import annotations

import os
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

_qdrant_client: QdrantClient | None = None


def _qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    return _qdrant_client


def _scroll_by_filter(collection_name: str, query_filter: Filter | None) -> list[dict]:
    """Paginates scroll() to collect every matching point's payload — Qdrant returns
    results a page at a time, unlike ChromaDB's get(), which returned everything in one call."""
    payloads: list[dict] = []
    offset = None
    while True:
        records, offset = _qdrant().scroll(
            collection_name=collection_name,
            scroll_filter=query_filter,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        payloads.extend(r.payload or {} for r in records)
        if offset is None:
            break
    return payloads


@lru_cache(maxsize=16)
def _full_collection_documents(collection_name: str) -> tuple[tuple[str, str], ...]:
    """
    Cached (document, parent_id) pairs for every point in the collection. ChromaDB's
    `where_document={"$contains": ...}` has no Qdrant equivalent (Qdrant's payload text
    match is token-based, not substring), so get_parent_ids_for_chunks below preserves
    identical substring-match behavior by scanning this in Python instead. Cached per
    collection since spec collections only change when a spec is re-indexed (rare) — not
    invalidated automatically, matching this module's existing process-lifetime client
    caching pattern (the worker process restarts after any re-index anyway).
    """
    try:
        payloads = _scroll_by_filter(collection_name, query_filter=None)
    except Exception:
        return ()
    return tuple((p.get("document", ""), p.get("parent_id", "")) for p in payloads)


def fetch_parent_texts(
    collection_name: str,
    parent_ids: list[str],
) -> list[str]:
    """
    Retrieve all child chunks belonging to the given parent IDs.
    Concatenates them to reconstruct the full clause context.
    This gives the LLM the complete spec clause, not just a 500-char fragment.
    """
    parent_texts: list[str] = []

    for parent_id in parent_ids:
        try:
            payloads = _scroll_by_filter(
                collection_name,
                query_filter=Filter(must=[FieldCondition(key="parent_id", match=MatchValue(value=parent_id))]),
            )
        except Exception:
            continue
        if not payloads:
            continue
        # Sort by chunk_index to reassemble in order
        pairs = sorted(payloads, key=lambda p: p.get("chunk_index", 0))
        full_text = "\n".join(p.get("document", "") for p in pairs)
        parent_texts.append(full_text)

    return parent_texts


def get_parent_ids_for_chunks(
    collection_name: str,
    chunk_texts: list[str],
) -> list[str]:
    """
    Look up the parent_id for each chunk text by matching document content.
    Returns unique parent IDs.
    """
    try:
        corpus = _full_collection_documents(collection_name)
    except Exception:
        return []

    parent_ids: list[str] = []
    for text in chunk_texts:
        needle = text[:100]
        for document, parent_id in corpus:
            if needle in document:
                if parent_id and parent_id not in parent_ids:
                    parent_ids.append(parent_id)
                break

    return parent_ids
