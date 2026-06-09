from __future__ import annotations

from dataclasses import dataclass

from src.rag.indexing.structurer import SpecSection

# Fixed 500-char chunks with 50-char overlap.
# Exp01 and Exp05 both use this — consistently outperforms clause-boundary chunking.
# Clause chunks produced diffuse embeddings and collapsed context_precision to 0.31.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Sub-split threshold: clauses larger than this are split before chunking.
# Required because some spec clauses can be 18,000+ chars (Problem 6, Experiment findings).
# MAX_EMBED_CHARS guard is 28,000 — sub-splitting at 6,000 keeps us well under.
MAX_CLAUSE_CHARS = 6000
SUB_SPLIT_OVERLAP = 200


@dataclass
class SpecChunk:
    chunk_id: str
    parent_id: str      # ID of the full SpecSection (for parent fetcher)
    authority: str
    network: str
    source_file: str
    division: str
    section: str
    clause: str
    chunk_index: int    # position within the section
    text: str


def _fixed_chunks(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into fixed-size chunks with overlap."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start >= len(text):
            break
    return [c.strip() for c in chunks if c.strip()]


def chunk_section(section: SpecSection, section_index: int) -> list[SpecChunk]:
    """
    Split a SpecSection into child chunks.
    Large sections are first sub-split to prevent OpenAI token limit errors.
    """
    parent_id = f"{section.authority}_{section.network}_{section_index:05d}"
    text = section.text

    # Sub-split oversized clauses before fixed chunking
    if len(text) > MAX_CLAUSE_CHARS:
        sub_texts = _fixed_chunks(text, size=MAX_CLAUSE_CHARS, overlap=SUB_SPLIT_OVERLAP)
    else:
        sub_texts = [text]

    all_chunks: list[SpecChunk] = []
    chunk_idx = 0
    for sub_text in sub_texts:
        for fragment in _fixed_chunks(sub_text):
            all_chunks.append(SpecChunk(
                chunk_id=f"{parent_id}_c{chunk_idx:04d}",
                parent_id=parent_id,
                authority=section.authority,
                network=section.network,
                source_file=section.source_file,
                division=section.division,
                section=section.section,
                clause=section.clause,
                chunk_index=chunk_idx,
                text=fragment,
            ))
            chunk_idx += 1

    return all_chunks


def chunk_sections(sections: list[SpecSection]) -> list[SpecChunk]:
    all_chunks: list[SpecChunk] = []
    for i, section in enumerate(sections):
        all_chunks.extend(chunk_section(section, i))
    return all_chunks
