from __future__ import annotations

import os
from pathlib import Path

import chromadb
from openai import OpenAI

from src.rag.indexing.chunker import SpecChunk, chunk_sections
from src.rag.indexing.metadata_tagger import tag_chunks
from src.rag.indexing.pdf_loader import load_spec_pdf
from src.rag.indexing.structurer import structure_pages

# Batch size for ChromaDB add() calls.
# Hard limit is 1,000 — use 500 for a safe margin (Problem 4, experiment findings).
CHROMA_BATCH_SIZE = 500

# OpenAI embedding model — must match the model used at query time.
# Never use collection.query(query_texts=...) — always pre-embed and use query_embeddings.
EMBED_MODEL = "text-embedding-3-small"

# Hard truncation before any text reaches the embedding API.
# text-embedding-3-small token limit is 8,192 tokens (~28,000 chars @ ~3.4 chars/token).
MAX_EMBED_CHARS = 28_000

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


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, applying hard truncation before sending."""
    truncated = [t[:MAX_EMBED_CHARS] for t in texts]
    response = _openai().embeddings.create(model=EMBED_MODEL, input=truncated)
    return [item.embedding for item in response.data]


def _add_chunks_to_collection(
    collection: chromadb.Collection,
    chunks: list[SpecChunk],
    metadatas: list[dict],
) -> None:
    """Add chunks in batches of CHROMA_BATCH_SIZE to stay under the 1,000 limit."""
    for start in range(0, len(chunks), CHROMA_BATCH_SIZE):
        batch_chunks = chunks[start : start + CHROMA_BATCH_SIZE]
        batch_meta = metadatas[start : start + CHROMA_BATCH_SIZE]
        texts = [c.text for c in batch_chunks]
        ids = [c.chunk_id for c in batch_chunks]
        embeddings = _embed(texts)
        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=batch_meta,
        )
        print(f"  Indexed {start + len(batch_chunks):,} / {len(chunks):,} chunks")


def index_spec_pdf(
    pdf_path: Path | str,
    authority: str,
    network: str,
    collection_name: str | None = None,
    reset: bool = False,
) -> int:
    """
    Full pipeline: PDF → pages → sections → chunks → embed → store.
    Returns the number of chunks indexed.
    """
    collection_name = collection_name or f"{authority.lower()}_specifications"
    pdf_path = Path(pdf_path)

    print(f"Loading {pdf_path.name} ({authority} / {network})")
    pages = load_spec_pdf(pdf_path, authority, network)
    print(f"  Loaded {len(pages)} pages")

    sections = structure_pages(pages)
    print(f"  Structured into {len(sections)} sections")

    chunks = chunk_sections(sections)
    metadatas = tag_chunks(chunks)
    print(f"  Created {len(chunks)} chunks")

    collection = _chroma().get_or_create_collection(name=collection_name)
    if reset:
        _chroma().delete_collection(name=collection_name)
        collection = _chroma().create_collection(name=collection_name)

    _add_chunks_to_collection(collection, chunks, metadatas)
    print(f"  Done. Total in collection: {collection.count()}")
    return len(chunks)
