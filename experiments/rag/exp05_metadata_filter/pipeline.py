"""
Experiment 05 — Hybrid Search + Cohere Reranking + Metadata Filtering

What changes vs exp04:
  Chunking:   Fixed 500-char  (same)
  Retrieval:  BM25 top-20 + Semantic top-20 with metadata pre-filter by network
              → RRF → top-20 candidates
  Reranking:  Cohere rerank-english-v3.0 → top 5
  Filtering:  ChromaDB where={"network": network} applied BEFORE semantic search
              BM25 also filtered to same network's documents only

Hypothesis: Constraining both BM25 and semantic search to the relevant spec
network (irrigation / storm_water / road) eliminates cross-network noise that
caused context_precision to collapse in exp03/04. Should restore or exceed
exp01's precision (0.8595) while keeping the recall gains from hybrid search.

Collection: adm_specifications
retrieve(question, item) — item dict from golden dataset carries {"network": ...}
"""

from __future__ import annotations

import os
from functools import lru_cache

import chromadb
import cohere
from dotenv import load_dotenv
from openai import OpenAI
from rank_bm25 import BM25Okapi

load_dotenv()

CHROMA_LOCAL_PATH = "data/chromadb"
COLLECTION_NAME = "adm_specifications"
TOP_K = 5
RRF_CANDIDATES = 20
BM25_CANDIDATES = 20
SEMANTIC_CANDIDATES = 20
RRF_K = 60
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
COHERE_RERANK_MODEL = "rerank-english-v3.0"

_SYSTEM_PROMPT = (
    "You are a construction specification expert for ADM (Abu Dhabi Municipality) "
    "technical specifications. Answer the question using ONLY the provided context. "
    "Be precise and quote specific values, standards, and requirements. "
    "If the answer is not in the context, say: Not found in the provided context."
)


@lru_cache(maxsize=1)
def _collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=CHROMA_LOCAL_PATH)
    return client.get_collection(name=COLLECTION_NAME)


@lru_cache(maxsize=1)
def _openai() -> OpenAI:
    return OpenAI()


@lru_cache(maxsize=1)
def _cohere() -> cohere.ClientV2:
    return cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])


@lru_cache(maxsize=1)
def _all_docs_by_network() -> dict[str, tuple[list[str], list[str]]]:
    """
    Load all documents grouped by network for per-network BM25 indexing.
    Returns {network: (ids, docs)} cached after first call.
    """
    col = _collection()
    print(f"  Loading all docs for per-network BM25 indexes (one-time cost)...")
    data = col.get(include=["documents", "metadatas"])
    ids: list[str] = data["ids"]
    docs: list[str] = data["documents"]
    metas: list[dict] = data["metadatas"]

    grouped: dict[str, tuple[list[str], list[str]]] = {}
    for id_, doc, meta in zip(ids, docs, metas):
        net = meta.get("network", "unknown")
        if net not in grouped:
            grouped[net] = ([], [])
        grouped[net][0].append(id_)
        grouped[net][1].append(doc)

    for net, (net_ids, _) in grouped.items():
        print(f"    {net}: {len(net_ids)} chunks")
    return grouped


@lru_cache(maxsize=4)
def _bm25_for_network(network: str) -> tuple[BM25Okapi, list[str], list[str]]:
    """Build (or return cached) BM25 index for a specific network."""
    grouped = _all_docs_by_network()
    if network not in grouped:
        raise ValueError(f"Unknown network: {network!r}. Available: {list(grouped.keys())}")
    ids, docs = grouped[network]
    tokenized = [d.lower().split() for d in docs]
    bm25 = BM25Okapi(tokenized)
    return bm25, ids, docs


def _rrf_combine(semantic_ids: list[str], bm25_ids: list[str]) -> list[str]:
    """Reciprocal Rank Fusion over two ranked id lists."""
    all_ids = list(dict.fromkeys(semantic_ids + bm25_ids))
    sem_rank = {id_: i + 1 for i, id_ in enumerate(semantic_ids)}
    bm25_rank = {id_: i + 1 for i, id_ in enumerate(bm25_ids)}

    def rrf_score(id_: str) -> float:
        score = 0.0
        if id_ in sem_rank:
            score += 1.0 / (RRF_K + sem_rank[id_])
        if id_ in bm25_rank:
            score += 1.0 / (RRF_K + bm25_rank[id_])
        return score

    return sorted(all_ids, key=rrf_score, reverse=True)


def retrieve(question: str, item: dict) -> list[str]:
    """
    Network-filtered BM25 + semantic → RRF top-20 → Cohere rerank → top 5.

    Args:
        question: Natural language question.
        item:     Golden dataset item dict — must contain {"network": str}.
    """
    network = item.get("network", "")

    # --- Per-network BM25 retrieval ---
    bm25, net_ids, net_docs = _bm25_for_network(network)
    id_to_doc = dict(zip(net_ids, net_docs))

    query_tokens = question.lower().split()
    bm25_scores = bm25.get_scores(query_tokens)
    top_bm25_indices = sorted(
        range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True
    )[:BM25_CANDIDATES]
    bm25_top_ids = [net_ids[i] for i in top_bm25_indices]

    # --- Network-filtered semantic retrieval ---
    embedding = (
        _openai()
        .embeddings.create(model=EMBED_MODEL, input=[question])
        .data[0]
        .embedding
    )
    sem_result = _collection().query(
        query_embeddings=[embedding],
        n_results=SEMANTIC_CANDIDATES,
        where={"network": network},
        include=[],
    )
    semantic_ids: list[str] = sem_result["ids"][0]

    # --- RRF fusion → top-20 candidates ---
    fused_ids = _rrf_combine(semantic_ids, bm25_top_ids)[:RRF_CANDIDATES]
    candidates = [id_to_doc[id_] for id_ in fused_ids if id_ in id_to_doc]

    if not candidates:
        return []

    # --- Cohere reranking → top 5 ---
    rerank_response = _cohere().rerank(
        query=question,
        documents=candidates,
        top_n=TOP_K,
        model=COHERE_RERANK_MODEL,
    )
    return [candidates[r.index] for r in rerank_response.results]


def generate(question: str, contexts: list[str]) -> str:
    """Generate answer from filtered + reranked contexts using GPT-4o-mini."""
    context_block = "\n\n---\n\n".join(
        f"[Context {i + 1}]\n{ctx}" for i, ctx in enumerate(contexts)
    )
    user_msg = f"Context:\n{context_block}\n\nQuestion: {question}"

    response = _openai().chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0,
        max_tokens=350,
    )
    return response.choices[0].message.content.strip()
