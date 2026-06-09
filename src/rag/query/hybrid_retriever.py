from __future__ import annotations

from functools import lru_cache

import chromadb
from openai import OpenAI
from rank_bm25 import BM25Okapi

from src.rag.query.query_constructor import StructuredQuery

# Promoted directly from experiments/rag/exp05_metadata_filter/pipeline.py.
# These constants are proven — do not change without re-running RAGAS evaluation.
TOP_K = 5
RRF_CANDIDATES = 20
BM25_CANDIDATES = 20
SEMANTIC_CANDIDATES = 20
RRF_K = 60

EMBED_MODEL = "text-embedding-3-small"
_LOCAL_CHROMA_PATH = "data/chromadb"

_openai_client: OpenAI | None = None
_chroma_client: chromadb.PersistentClient | None = None


def _openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def _chroma() -> chromadb.PersistentClient:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=_LOCAL_CHROMA_PATH)
    return _chroma_client


def _get_collection(collection_name: str) -> chromadb.Collection:
    return _chroma().get_collection(name=collection_name)


@lru_cache(maxsize=4)
def _build_bm25_for_network(
    collection_name: str, network: str
) -> tuple[BM25Okapi, list[str], list[str]]:
    """
    Build a per-network BM25 index from ChromaDB.
    Cached — only built once per (collection, network) pair per process.
    Filtering BM25 to the correct network eliminates cross-spec-book noise
    that collapsed context_precision in exp03 (0.51 vs exp01's 0.86).
    """
    col = _get_collection(collection_name)
    data = col.get(where={"network": network}, include=["documents"])
    ids: list[str] = data["ids"]
    docs: list[str] = data["documents"]
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


def retrieve_candidates(
    query: StructuredQuery,
    collection_name: str,
) -> list[str]:
    """
    Network-filtered BM25 + semantic → RRF top-20 candidates.
    Returns documents (not IDs) for the reranker.
    Faithfulness = 1.0 and best recall achieved with this configuration (exp05).
    """
    col = _get_collection(collection_name)
    network = query.network

    # Per-network BM25
    bm25, net_ids, net_docs = _build_bm25_for_network(collection_name, network)
    id_to_doc = dict(zip(net_ids, net_docs))
    query_tokens = query.question.lower().split()
    scores = bm25.get_scores(query_tokens)
    top_bm25_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:BM25_CANDIDATES]
    bm25_top_ids = [net_ids[i] for i in top_bm25_indices]

    # Network-filtered semantic search
    embedding = (
        _openai()
        .embeddings.create(model=EMBED_MODEL, input=[query.question])
        .data[0]
        .embedding
    )
    sem_result = col.query(
        query_embeddings=[embedding],
        n_results=SEMANTIC_CANDIDATES,
        where=query.metadata_filter,
        include=[],
    )
    semantic_ids: list[str] = sem_result["ids"][0]

    # RRF fusion
    fused_ids = _rrf_combine(semantic_ids, bm25_top_ids)[:RRF_CANDIDATES]
    return [id_to_doc[id_] for id_ in fused_ids if id_ in id_to_doc]
