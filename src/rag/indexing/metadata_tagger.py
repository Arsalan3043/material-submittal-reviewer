from __future__ import annotations

from src.rag.indexing.chunker import SpecChunk


def build_metadata(chunk: SpecChunk) -> dict:
    """
    Build the ChromaDB metadata dict for a chunk.
    The 'network' field is the primary filter key from Exp05 —
    restricting BM25 and semantic search to one spec book eliminates
    cross-network noise and achieves faithfulness = 1.0.
    """
    return {
        "authority":   chunk.authority,
        "network":     chunk.network,      # exp05 filter key
        "division":    chunk.division,
        "section":     chunk.section,
        "clause":      chunk.clause,
        "source_file": chunk.source_file,
        "chunk_index": chunk.chunk_index,
        "parent_id":   chunk.parent_id,
    }


def tag_chunks(chunks: list[SpecChunk]) -> list[dict]:
    """Return a metadata dict for every chunk (same order as chunks list)."""
    return [build_metadata(c) for c in chunks]
