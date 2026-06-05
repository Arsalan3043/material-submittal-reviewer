"""
Experiment 04 — Hybrid Search + Cohere Reranking

What changes vs exp03:
  Chunking:   Fixed 500-char  (same)
  Retrieval:  BM25 top-20 + Semantic top-20 → RRF → top-20 candidates
  Reranking:  Cohere rerank-english-v3.0 → top 5
  Filtering:  None

Hypothesis: Cross-encoder reranking filters out the noisy BM25 keyword matches
that hurt context_precision in exp03, while keeping the recall gains from
hybrid retrieval. Faithfulness and context_recall should hold or improve.

Collection: adm_specifications  (same fixed-chunk collection)
Requires:   COHERE_API_KEY in .env
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
RRF_CANDIDATES = 20   # top-N kept after RRF, fed to Cohere
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
def _bm25_index() -> tuple[BM25Okapi, list[str], list[str]]:
    """Load all docs and build BM25 index. Cached after first call."""
    col = _collection()
    print(f"  Building BM25 index from {col.count()} docs (one-time cost)...")
    data = col.get(include=["documents"])
    ids: list[str] = data["ids"]
    docs: list[str] = data["documents"]
    tokenized = [d.lower().split() for d in docs]
    bm25 = BM25Okapi(tokenized)
    print(f"  BM25 index ready ({len(ids)} docs)")
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


def retrieve(question: str) -> list[str]:
    """
    BM25 top-20 + semantic top-20 → RRF top-20 → Cohere rerank → top 5.
    """
    bm25, all_ids, all_docs = _bm25_index()
    id_to_doc = dict(zip(all_ids, all_docs))

    # Semantic retrieval
    embedding = (
        _openai()
        .embeddings.create(model=EMBED_MODEL, input=[question])
        .data[0]
        .embedding
    )
    sem_result = _collection().query(
        query_embeddings=[embedding],
        n_results=SEMANTIC_CANDIDATES,
        include=[],
    )
    semantic_ids: list[str] = sem_result["ids"][0]

    # BM25 retrieval
    query_tokens = question.lower().split()
    bm25_scores = bm25.get_scores(query_tokens)
    top_bm25_indices = sorted(
        range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True
    )[:BM25_CANDIDATES]
    bm25_top_ids = [all_ids[i] for i in top_bm25_indices]

    # RRF fusion → top 20 candidates for reranking
    fused_ids = _rrf_combine(semantic_ids, bm25_top_ids)[:RRF_CANDIDATES]
    candidates = [id_to_doc[id_] for id_ in fused_ids if id_ in id_to_doc]

    if not candidates:
        return []

    # Cohere reranking: top 20 → top 5
    rerank_response = _cohere().rerank(
        query=question,
        documents=candidates,
        top_n=TOP_K,
        model=COHERE_RERANK_MODEL,
    )
    return [candidates[r.index] for r in rerank_response.results]


def generate(question: str, contexts: list[str]) -> str:
    """Generate answer from reranked contexts using GPT-4o-mini."""
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
