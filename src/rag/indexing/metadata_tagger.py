from __future__ import annotations

from src.rag.indexing.chunker import SpecChunk


def build_metadata(chunk: SpecChunk) -> dict:
    """
    Build the ChromaDB metadata dict for a chunk.

    Key fields and their roles:

    network      — admin-typed spec book label (e.g. "storm_water", "irrigation",
                   "road").  Used as BM25 corpus scope in exp05.

    section      — 5-digit section number or chapter number extracted from the
                   PDF footer (e.g. "02810", "01027", "2").  This is now the
                   primary retrieval filter key — it is set from the authoritative
                   footer text rather than body-text regex matching, so it is
                   reliable even when spec headers say "SECTION 02810" instead of
                   a digit-first line that the old structurer required.

    clause       — subsection number from body text (e.g. "26.3.2") or equals
                   section when no subsections were detected.

    chapter_name — road specs only: normalised chapter name (e.g. "earthworks").
                   Empty string for division/section specs.  Included so BM25
                   can match chapter names when only a name (not number) appears
                   on the submittal cover page.

    division     — two-digit division code (e.g. "02") from footer.

    parent_id    — links child chunks back to their parent SpecSection for the
                   parent-fetcher step (retrieves full subsection context).
    """
    return {
        "authority":    chunk.authority,
        "network":      chunk.network,
        "division":     chunk.division,
        "section":      chunk.section,
        "clause":       chunk.clause,
        "chapter_name": chunk.chapter_name,
        "source_file":  chunk.source_file,
        "chunk_index":  chunk.chunk_index,
        "parent_id":    chunk.parent_id,
    }


def tag_chunks(chunks: list[SpecChunk]) -> list[dict]:
    """Return a metadata dict for every chunk (same order as chunks list)."""
    return [build_metadata(c) for c in chunks]
