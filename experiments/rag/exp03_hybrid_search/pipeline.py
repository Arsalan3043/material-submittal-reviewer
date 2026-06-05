"""
Experiment 03 — Hybrid Search (BM25 + Semantic + RRF)

What changes vs exp01:
  Chunking:   Fixed 500-char  (same as exp01 — outperformed clause chunking in exp02)
  Retrieval:  BM25 top-20 + Semantic top-20, fused via Reciprocal Rank Fusion → top 5
  Reranking:  None
  Filtering:  None

Hypothesis: BM25 term matching catches exact spec references (ASTM C494, clause
numbers, standard codes) that semantic search misses, improving context precision
and recall over the exp01 baseline.

Collection: adm_specifications  (same fixed-chunk collection as exp01)
"""

from __future__ import annotations

from functools import lru_cache

import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from rank_bm25 import BM25Okapi

load_dotenv()

CHROMA_LOCAL_PATH = "data/chromadb"
COLLECTION_NAME = "adm_specifications"
TOP_K = 5
BM25_CANDIDATES = 20
SEMANTIC_CANDIDATES = 20
RRF_K = 60
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"

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
    """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank(d)) across both lists."""
    all_ids = list(dict.fromkeys(semantic_ids + bm25_ids))  # deduplicated, order preserved

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
    BM25 top-20 + semantic top-20, fused via RRF, returns top-5 doc texts.
    BM25 index is built once on first call and cached for the full evaluation run.
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

    # RRF fusion → top 5
    fused_ids = _rrf_combine(semantic_ids, bm25_top_ids)[:TOP_K]
    return [id_to_doc[id_] for id_ in fused_ids if id_ in id_to_doc]


def generate(question: str, contexts: list[str]) -> str:
    """Generate answer from hybrid-retrieved contexts using GPT-4o-mini."""
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
