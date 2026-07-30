from __future__ import annotations

import os
import re
from functools import lru_cache

from langsmith.wrappers import wrap_openai
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from rank_bm25 import BM25Okapi

from src.rag.query.query_constructor import StructuredQuery

# Promoted directly from experiments/rag/exp05_metadata_filter/pipeline.py.
# These constants are proven — do not change without re-running RAGAS evaluation.
TOP_K = 5
RRF_CANDIDATES = 20
BM25_CANDIDATES = 20
SEMANTIC_CANDIDATES = 20
RRF_K = 60

# Qdrant's scroll() is paginated — this is the page size used to walk a full filtered (or
# unfiltered) corpus for BM25, not a result-count cap (unlike ChromaDB's single get() call,
# which returned everything at once).
SCROLL_PAGE_SIZE = 500

EMBED_MODEL = "text-embedding-3-small"

_openai_client: OpenAI | None = None
_qdrant_client: QdrantClient | None = None


def _openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = wrap_openai(OpenAI())
    return _openai_client


def _qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    return _qdrant_client


def _scroll_all(collection_name: str, query_filter: Filter | None) -> tuple[list[str], list[str]]:
    """Walks every point matching query_filter (or the whole collection when None),
    paginating with scroll() since Qdrant returns results a page at a time rather than
    all-at-once like ChromaDB's get(). Returns (ids, documents) in the same shape the old
    ChromaDB-backed _build_bm25 expected."""
    ids: list[str] = []
    docs: list[str] = []
    offset = None
    while True:
        records, offset = _qdrant().scroll(
            collection_name=collection_name,
            scroll_filter=query_filter,
            limit=SCROLL_PAGE_SIZE,
            offset=offset,
            with_payload=["document"],
            with_vectors=False,
        )
        for r in records:
            ids.append(str(r.id))
            docs.append((r.payload or {}).get("document", ""))
        if offset is None:
            break
    return ids, docs


@lru_cache(maxsize=64)
def _build_bm25(
    collection_name: str,
    filter_key: str,
    filter_value: str,
) -> tuple[BM25Okapi, list[str], list[str]]:
    """
    Filtered BM25 — cached per (collection, key, value).
    filter_key / filter_value are the Qdrant payload field and value to scope the corpus.
    Call with filter_key="" to build over the full collection.
    """
    query_filter = (
        Filter(must=[FieldCondition(key=filter_key, match=MatchValue(value=filter_value))])
        if filter_key
        else None
    )
    try:
        ids, docs = _scroll_all(collection_name, query_filter)
    except Exception:
        return BM25Okapi([["placeholder"]]), [], []
    if not ids:
        return BM25Okapi([["placeholder"]]), ids, docs
    tokenized = [d.lower().split() for d in docs]
    bm25 = BM25Okapi(tokenized)
    return bm25, ids, docs


def _rrf_combine(
    semantic_ids: list[str], bm25_ids: list[str]
) -> list[str]:
    """Reciprocal Rank Fusion over two ranked id lists."""
    all_ids = list(dict.fromkeys(semantic_ids + bm25_ids))
    sem_rank = {id_: i + 1 for i, id_ in enumerate(semantic_ids)}
    bm25_rank = {id_: i + 1 for i, id_ in enumerate(bm25_ids)}

    def score(id_: str) -> float:
        s = 0.0
        if id_ in sem_rank:
            s += 1.0 / (RRF_K + sem_rank[id_])
        if id_ in bm25_rank:
            s += 1.0 / (RRF_K + bm25_rank[id_])
        return s

    return sorted(all_ids, key=score, reverse=True)


def _bm25_top_ids(
    collection_name: str,
    filter_key: str,
    filter_value: str,
    query_tokens: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Return (ranked_ids, id→doc map) from a filtered BM25 search."""
    bm25, ids, docs = _build_bm25(collection_name, filter_key, filter_value)
    if not ids:
        return [], {}
    scores = bm25.get_scores(query_tokens)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:BM25_CANDIDATES]
    top_ids = [ids[i] for i in top_indices]
    return top_ids, dict(zip(ids, docs))


def retrieve_candidates(
    query: StructuredQuery,
    collection_name: str,
) -> list[str]:
    """
    BM25 + semantic → RRF top-20 candidates.

    Retrieval strategy (in order of preference):

    1. Section-scoped  — filter by {"section": clause_hint} when a ≥4-digit
       section number is available.  This is the most precise corpus.
    2. Network-scoped  — filter by {"network": network} when no clean section
       number is available (e.g. only a 2-digit division is known).
    3. Full-collection fallback — when the scoped search returns nothing (common
       when section metadata was not populated at index time, e.g. when the spec
       PDF headers use "SECTION 02810" format instead of a line starting with
       digits).  In fallback mode the normalized clause number in the BM25
       question string still ranks the correct chunks near the top.

    The fallback is what fixes the "Clause ADM Specs Div-02-Section 02810 was not
    found" error: the old code had no fallback, so a metadata filter miss meant
    zero results every time.
    """
    if not _qdrant().collection_exists(collection_name):
        return []

    clause = query.clause_hint          # e.g. "02810" or "earthworks" (normalised)
    query_tokens = query.question.lower().split()

    # ── BM25 — choose corpus by what kind of clause identifier we have ────────
    has_section_num  = bool(clause and re.search(r'\d{4,}', clause))
    has_chapter_name = bool(clause and not re.search(r'\d', clause))  # text only

    if has_section_num:
        bm25_ids, id_to_doc = _bm25_top_ids(collection_name, "section", clause, query_tokens)
    elif has_chapter_name:
        bm25_ids, id_to_doc = _bm25_top_ids(collection_name, "chapter_name", clause, query_tokens)
    else:
        bm25_ids, id_to_doc = _bm25_top_ids(collection_name, "network", query.network, query_tokens)

    # Fallback: scoped filter matched zero documents — search full collection.
    # The clause number / chapter name in the BM25 question string still ranks
    # the correct chunks near the top via term frequency.
    if not bm25_ids:
        bm25_ids, id_to_doc = _bm25_top_ids(collection_name, "", "", query_tokens)

    # ── Semantic search ───────────────────────────────────────────────────────
    try:
        embedding = (
            _openai()
            .embeddings.create(model=EMBED_MODEL, input=[query.question])
            .data[0]
            .embedding
        )

        def _semantic_query(where: dict | None) -> list[str]:
            query_filter = (
                Filter(must=[FieldCondition(key=k, match=MatchValue(value=v)) for k, v in where.items()])
                if where
                else None
            )
            result = _qdrant().query_points(
                collection_name=collection_name,
                query=embedding,
                query_filter=query_filter,
                limit=SEMANTIC_CANDIDATES,
                with_payload=False,
            )
            return [str(p.id) for p in result.points]

        semantic_ids = _semantic_query(query.metadata_filter)

        # Fallback: metadata filter matched nothing — search full collection
        if not semantic_ids:
            semantic_ids = _semantic_query(None)

    except Exception:
        semantic_ids = []

    if not bm25_ids and not semantic_ids:
        return []

    # ── RRF fusion ────────────────────────────────────────────────────────────
    fused_ids = _rrf_combine(semantic_ids, bm25_ids)[:RRF_CANDIDATES]
    return [id_to_doc[id_] for id_ in fused_ids if id_ in id_to_doc]
