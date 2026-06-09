from __future__ import annotations

import chromadb

_LOCAL_CHROMA_PATH = "data/chromadb"
CHROMA_BATCH_SIZE = 500

_client: chromadb.PersistentClient | None = None


def _chroma() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=_LOCAL_CHROMA_PATH)
    return _client


def _collection_name(submittal_id: str) -> str:
    return f"submittal_{submittal_id}"


def create_submittal_collection(submittal_id: str) -> chromadb.Collection:
    """Create (or recreate) the per-session collection for a submittal."""
    name = _collection_name(submittal_id)
    # Delete if exists to ensure a fresh collection
    try:
        _chroma().delete_collection(name=name)
    except Exception:
        pass
    return _chroma().create_collection(name=name)


def store_embeddings(
    submittal_id: str,
    embedded: list[tuple[str, str, list[float], dict]],
) -> None:
    """
    Store pre-computed embeddings into the per-session collection.
    Batched at CHROMA_BATCH_SIZE to stay under the 1,000-record add() limit.
    """
    col = create_submittal_collection(submittal_id)
    for start in range(0, len(embedded), CHROMA_BATCH_SIZE):
        batch = embedded[start : start + CHROMA_BATCH_SIZE]
        col.add(
            ids=[b[0] for b in batch],
            documents=[b[1] for b in batch],
            embeddings=[b[2] for b in batch],
            metadatas=[b[3] for b in batch],
        )


def delete_submittal_collection(submittal_id: str) -> None:
    """Clean up after session ends."""
    try:
        _chroma().delete_collection(name=_collection_name(submittal_id))
    except Exception:
        pass


def collection_exists(submittal_id: str) -> bool:
    try:
        _chroma().get_collection(name=_collection_name(submittal_id))
        return True
    except Exception:
        return False
