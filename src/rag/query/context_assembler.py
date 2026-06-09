from __future__ import annotations

from src.rag.query.hybrid_retriever import retrieve_candidates
from src.rag.query.parent_fetcher import fetch_parent_texts, get_parent_ids_for_chunks
from src.rag.query.query_constructor import StructuredQuery
from src.rag.query.reranker import rerank

# If RAG retrieval returns empty, the agent must flag "spec clause not found"
# rather than hallucinating. This sentinel triggers that path.
EMPTY_CONTEXT_SENTINEL = "__SPEC_NOT_FOUND__"


def assemble_spec_context(
    query: StructuredQuery,
    collection_name: str,
    use_parent_fetch: bool = True,
) -> str:
    """
    Full exp05 query pipeline:
      Hybrid retrieval → Cohere reranking → parent fetch → formatted context string.

    Returns EMPTY_CONTEXT_SENTINEL if nothing is retrieved (agent handles gracefully).
    """
    candidates = retrieve_candidates(query, collection_name)
    if not candidates:
        return EMPTY_CONTEXT_SENTINEL

    reranked = rerank(query.question, candidates)
    if not reranked:
        return EMPTY_CONTEXT_SENTINEL

    if use_parent_fetch:
        parent_ids = get_parent_ids_for_chunks(collection_name, reranked)
        if parent_ids:
            full_texts = fetch_parent_texts(collection_name, parent_ids)
            context_chunks = full_texts if full_texts else reranked
        else:
            context_chunks = reranked
    else:
        context_chunks = reranked

    return _format_context(context_chunks)


def _format_context(chunks: list[str]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[Context {i}]\n{chunk.strip()}")
    return "\n\n---\n\n".join(parts)
