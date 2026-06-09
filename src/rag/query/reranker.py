from __future__ import annotations

import os

import cohere

# Cross-encoder reranker recovered 38% of precision lost in exp03 (0.51 → 0.70).
# Combined with metadata filtering (exp05), achieves faithfulness = 1.0.
_RERANK_MODEL = "rerank-english-v3.0"
TOP_N = 5

_client: cohere.ClientV2 | None = None


def _cohere() -> cohere.ClientV2:
    global _client
    if _client is None:
        _client = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])
    return _client


def rerank(question: str, candidates: list[str]) -> list[str]:
    """
    Rerank candidate chunks using Cohere cross-encoder.
    Returns the top TOP_N most relevant documents.
    Input: RRF top-20 candidates. Output: top-5 for generation context.
    """
    if not candidates:
        return []
    response = _cohere().rerank(
        query=question,
        documents=candidates,
        top_n=min(TOP_N, len(candidates)),
        model=_RERANK_MODEL,
    )
    return [candidates[r.index] for r in response.results]
