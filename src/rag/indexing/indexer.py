from __future__ import annotations

import os
import uuid
from pathlib import Path

from langsmith.wrappers import wrap_openai
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.rag.indexing.chunker import SpecChunk, chunk_sections
from src.rag.indexing.metadata_tagger import tag_chunks
from src.rag.indexing.pdf_loader import load_spec_pdf
from src.rag.indexing.structurer import structure_pages

# Batch size for Qdrant upsert() calls (was ChromaDB's add() batch limit — Qdrant has no
# equivalent hard cap, but the same batch size keeps request payloads reasonable).
QDRANT_BATCH_SIZE = 500

# text-embedding-3-small produces 1536-dim vectors — must match at query time
# (src/rag/query/hybrid_retriever.py, src/rag/query/parent_fetcher.py).
EMBEDDING_DIM = 1536

# OpenAI embedding model — must match the model used at query time.
EMBED_MODEL = "text-embedding-3-small"

# Hard truncation before any text reaches the embedding API.
# text-embedding-3-small token limit is 8,192 tokens (~28,000 chars @ ~3.4 chars/token).
MAX_EMBED_CHARS = 28_000

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


def _point_id(chunk_id: str) -> str:
    """
    Qdrant point IDs must be an unsigned int or a UUID — chunk_id is an arbitrary string
    (e.g. "<parent_id>_c0004"), so it's deterministically hashed into a UUID. Same chunk_id
    always maps to the same point ID, so re-indexing upserts in place instead of duplicating.
    The original chunk_id is kept in the payload (see build_metadata callers) for anyone
    who needs the human-readable form back.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _ensure_collection(collection_name: str) -> None:
    if not _qdrant().collection_exists(collection_name):
        _qdrant().create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, applying hard truncation before sending."""
    truncated = [t[:MAX_EMBED_CHARS] for t in texts]
    response = _openai().embeddings.create(model=EMBED_MODEL, input=truncated)
    return [item.embedding for item in response.data]


def _add_chunks_to_collection(
    collection_name: str,
    chunks: list[SpecChunk],
    metadatas: list[dict],
) -> None:
    """Add chunks in batches to stay well under any single-request payload size."""
    for start in range(0, len(chunks), QDRANT_BATCH_SIZE):
        batch_chunks = chunks[start : start + QDRANT_BATCH_SIZE]
        batch_meta = metadatas[start : start + QDRANT_BATCH_SIZE]
        texts = [c.text for c in batch_chunks]
        embeddings = _embed(texts)
        points = [
            PointStruct(
                id=_point_id(chunk.chunk_id),
                vector=embedding,
                payload={**meta, "chunk_id": chunk.chunk_id, "document": chunk.text},
            )
            for chunk, meta, embedding in zip(batch_chunks, batch_meta, embeddings)
        ]
        _qdrant().upsert(collection_name=collection_name, points=points)
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

    if reset and _qdrant().collection_exists(collection_name):
        _qdrant().delete_collection(collection_name=collection_name)
    _ensure_collection(collection_name)

    _add_chunks_to_collection(collection_name, chunks, metadatas)
    count = _qdrant().count(collection_name=collection_name).count
    print(f"  Done. Total in collection: {count}")
    return len(chunks)
