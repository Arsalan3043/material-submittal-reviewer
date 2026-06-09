from __future__ import annotations

import chromadb

_LOCAL_CHROMA_PATH = "data/chromadb"
_chroma_client: chromadb.PersistentClient | None = None


def _chroma() -> chromadb.PersistentClient:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=_LOCAL_CHROMA_PATH)
    return _chroma_client


def fetch_parent_texts(
    collection_name: str,
    parent_ids: list[str],
) -> list[str]:
    """
    Retrieve all child chunks belonging to the given parent IDs.
    Concatenates them to reconstruct the full clause context.
    This gives the LLM the complete spec clause, not just a 500-char fragment.
    """
    col = _chroma().get_collection(name=collection_name)
    parent_texts: list[str] = []

    for parent_id in parent_ids:
        results = col.get(
            where={"parent_id": parent_id},
            include=["documents", "metadatas"],
        )
        if not results["ids"]:
            continue
        # Sort by chunk_index to reassemble in order
        pairs = sorted(
            zip(results["metadatas"], results["documents"]),
            key=lambda p: p[0].get("chunk_index", 0),
        )
        full_text = "\n".join(doc for _, doc in pairs)
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
    col = _chroma().get_collection(name=collection_name)
    parent_ids: list[str] = []

    for text in chunk_texts:
        results = col.get(
            where_document={"$contains": text[:100]},
            include=["metadatas"],
            limit=1,
        )
        if results["metadatas"]:
            pid = results["metadatas"][0].get("parent_id", "")
            if pid and pid not in parent_ids:
                parent_ids.append(pid)

    return parent_ids
