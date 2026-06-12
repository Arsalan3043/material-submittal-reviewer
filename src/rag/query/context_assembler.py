from __future__ import annotations

from functools import lru_cache

from src.rag.query.hybrid_retriever import retrieve_candidates
from src.rag.query.parent_fetcher import fetch_parent_texts, get_parent_ids_for_chunks
from src.rag.query.query_constructor import build_query
from src.rag.query.reranker import rerank

# If RAG retrieval returns empty, the agent must flag "spec clause not found"
# rather than hallucinating. This sentinel triggers that path.
EMPTY_CONTEXT_SENTINEL = "__SPEC_NOT_FOUND__"


@lru_cache(maxsize=64)
def _fetch_spec_context(clause_ref: str, authority: str) -> str:
    """
    Cached inner function — same (clause_ref, authority) always returns the same
    spec context within a process lifetime. The spec database only changes when
    specs are reindexed (an admin action), so caching indefinitely is safe.

    Both spec_verifier and table_auditor call assemble_spec_context for the same
    clause. Without this cache that is 2× embedding + 2× Cohere per review.
    With it: 1× embedding + 1× Cohere, second call is a free dict lookup.
    """
    from src.config import get_authority_profile

    profile = get_authority_profile(authority)
    collection_name = profile.chroma_collection_name

    query = build_query(
        spec_clause=clause_ref,
        material_description="",   # clause_ref alone is sufficient for retrieval
        authority=authority,
    )

    candidates = retrieve_candidates(query, collection_name)
    if not candidates:
        return EMPTY_CONTEXT_SENTINEL

    reranked = rerank(query.question, candidates)
    if not reranked:
        return EMPTY_CONTEXT_SENTINEL

    parent_ids = get_parent_ids_for_chunks(collection_name, reranked)
    if parent_ids:
        full_texts = fetch_parent_texts(collection_name, parent_ids)
        context_chunks = full_texts if full_texts else reranked
    else:
        context_chunks = reranked

    return _format_context(context_chunks)


def assemble_spec_context(
    clause_ref: str,
    authority: str,
    question: str | None = None,
) -> str:
    """
    Public API for all agents. Accepts an optional question kwarg for caller
    convenience, but the cache key is (clause_ref, authority) only — both
    spec_verifier and table_auditor get the same context from one RAG call.
    """
    return _fetch_spec_context(clause_ref, authority)


def _format_context(chunks: list[str]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[Context {i}]\n{chunk.strip()}")
    return "\n\n---\n\n".join(parts)
