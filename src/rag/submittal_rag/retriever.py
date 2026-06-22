from __future__ import annotations

import chromadb
from langsmith.wrappers import wrap_openai
from openai import OpenAI

_LOCAL_CHROMA_PATH = "data/chromadb"
EMBED_MODEL = "text-embedding-3-small"
TOP_K = 5

_openai_client: OpenAI | None = None
_chroma_client: chromadb.PersistentClient | None = None


def _openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = wrap_openai(OpenAI())
    return _openai_client


def _chroma() -> chromadb.PersistentClient:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=_LOCAL_CHROMA_PATH)
    return _chroma_client


def retrieve_from_submittal(
    question: str,
    submittal_id: str,
    doc_type_filter: str | None = None,
) -> list[tuple[str, str]]:
    """
    Retrieve relevant chunks from the per-session submittal collection.
    Returns list of (document_text, source_filename) tuples.

    Semantic-only retrieval (no BM25/reranking) — post-review Q&A queries
    are conversational, not exact-spec-term lookups, so pure semantic search
    is appropriate and sufficient here.
    """
    collection_name = f"submittal_{submittal_id}"
    try:
        col = _chroma().get_collection(name=collection_name)
    except Exception:
        return []

    embedding = (
        _openai()
        .embeddings.create(model=EMBED_MODEL, input=[question])
        .data[0]
        .embedding
    )

    where = {"doc_type": doc_type_filter} if doc_type_filter else None
    kwargs = {"query_embeddings": [embedding], "n_results": TOP_K, "include": ["documents", "metadatas"]}
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)
    if not results["ids"] or not results["ids"][0]:
        return []

    return [
        (doc, meta.get("filename", "unknown"))
        for doc, meta in zip(results["documents"][0], results["metadatas"][0])
    ]
