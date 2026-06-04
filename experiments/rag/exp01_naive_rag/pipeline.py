"""
Experiment 01 — Naive RAG Pipeline (Baseline)

Config:
  Chunking:   Fixed 500 chars (indexed by load_spec.py default)
  Retrieval:  Semantic only (text-embedding-3-small -> ChromaDB), top 5
  Reranking:  None
  Filtering:  None — searches all 3 networks indiscriminately

This is the baseline all other experiments are compared against.
"""

from __future__ import annotations

from functools import lru_cache

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CHROMA_LOCAL_PATH = "data/chromadb"
COLLECTION_NAME = "adm_specifications"
TOP_K = 5
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


def retrieve(question: str) -> list[str]:
    """
    Embed question -> query ChromaDB top 5. No metadata filter.
    Returns list of raw chunk texts.
    """
    embedding = (
        _openai()
        .embeddings.create(model=EMBED_MODEL, input=[question])
        .data[0]
        .embedding
    )
    result = _collection().query(
        query_embeddings=[embedding],
        n_results=TOP_K,
        include=["documents"],
    )
    return result["documents"][0]


def generate(question: str, contexts: list[str]) -> str:
    """
    Build a prompt from retrieved contexts and call GPT-4o-mini.
    Returns the answer string.
    """
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
