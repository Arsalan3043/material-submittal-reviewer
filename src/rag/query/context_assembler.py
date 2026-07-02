from __future__ import annotations

from functools import lru_cache

from src.rag.query.hybrid_retriever import retrieve_candidates
from src.rag.query.parent_fetcher import fetch_parent_texts, get_parent_ids_for_chunks
from src.rag.query.query_constructor import build_query, normalize_clause_ref
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
    convenience, but the cache key is (normalized_clause, authority) — both
    spec_verifier and table_auditor get the same context from one RAG call,
    and different phrasings of the same clause ("ADM Specs Div-02-Section 02810"
    vs "Section 02810") share the same cache entry.
    """
    return _fetch_spec_context(normalize_clause_ref(clause_ref), authority)


def assemble_spec_context_enriched(
    clause_ref: str,
    authority: str,
    material_description: str = "",
    spec_snippet: str = "",
) -> str:
    """
    Enriched (non-cached) retrieval that passes material_description and a
    snippet of the submitted spec text into the query embedding.

    Why not cached: material_description differs per review, so a cache keyed
    on (clause, authority) alone would serve stale context to a different
    material.  The enriched query is used only once per review (Phase 2 of
    spec_verifier), so the cache hit rate would be near-zero anyway.

    How enrichment helps:
    - material_description → "Detectable Warning Tape" lands in the question
      string, biasing the embedding toward relevant spec subsections.
    - spec_snippet → key terms from the submitted spec (e.g. "BS EN 12966",
      "26.3.2") are appended to the question, narrowing semantic search to the
      subsections the contractor actually referenced.
    """
    from src.config import get_authority_profile

    profile = get_authority_profile(authority)
    collection_name = profile.chroma_collection_name
    normalized = normalize_clause_ref(clause_ref)

    query = build_query(
        spec_clause=normalized,
        material_description=material_description,
        authority=authority,
    )

    # Append first 400 chars of submitted spec to enrich the semantic query.
    # Trim to avoid bloating the embedding input beyond useful signal.
    if spec_snippet.strip():
        enriched_question = (
            query.question
            + f" Submitted specification context: {spec_snippet[:400].strip()}"
        )
        from src.rag.query.query_constructor import StructuredQuery
        query = StructuredQuery(
            question=enriched_question,
            authority=query.authority,
            network=query.network,
            clause_hint=query.clause_hint,
            metadata_filter=query.metadata_filter,
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


def _format_context(chunks: list[str]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[Context {i}]\n{chunk.strip()}")
    return "\n\n---\n\n".join(parts)
