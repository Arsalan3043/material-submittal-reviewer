"""
Experiment 02 — Clause-Based Chunking

What changes vs exp01:
  Chunking:   Clause-boundary splits (not fixed 500 chars)
              Each chunk = one spec clause/sub-clause (natural document unit)
  Retrieval:  Semantic only, top 5  <- same as exp01
  Reranking:  None                  <- same as exp01
  Filtering:  None                  <- same as exp01

Hypothesis: Clause-aligned chunks give the LLM complete, coherent
requirements rather than mid-sentence fragments, improving faithfulness
and context recall.

Collection: adm_clause  (indexed by run.py on first run)
"""

from __future__ import annotations

from functools import lru_cache

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CHROMA_LOCAL_PATH = "data/chromadb"
COLLECTION_NAME = "adm_clause"
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
    Embed question -> query adm_clause collection, top 5. No metadata filter.
    Returns list of clause-aligned chunk texts.
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
    """Generate answer from clause-aligned contexts using GPT-4o-mini."""
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
