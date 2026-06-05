"""
ADM spec loader for RAG experiments.

Reads PDFs from data/specs/adm/, extracts text, chunks,
embeds with text-embedding-3-small, and stores in the
adm_specifications ChromaDB Cloud collection.

Supports two chunking strategies so all 5 experiments can reuse this:
  fixed  — fixed character-size chunks          (exp01 naive RAG baseline)
  clause — split at clause/section boundaries   (exp02+ clause chunking)

Usage:
    from experiments.rag.shared.load_spec import load_adm_specs

    # Exp01 — naive baseline
    load_adm_specs(chunking_strategy="fixed", chunk_size=500, chunk_overlap=50)

    # Exp02+ — clause-based
    load_adm_specs(chunking_strategy="clause", overwrite=True)

Run directly to load all three specs with default settings:
    python experiments/rag/shared/load_spec.py
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal

import chromadb
import fitz  # PyMuPDF
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

SPECS_DIR = Path("data/specs/adm")
AUTHORITY = "ADM"
COLLECTION_NAME = "adm_specifications"

# Map PDF stem -> canonical network name
NETWORK_MAP: dict[str, str] = {
    "storm_water": "storm_water",
    "irrigation": "irrigation",
    "road": "road",
    "street_light": "street_light",
}

# Matches "SECTION 33 40 00 - TITLE" or "SECTION 33 40 00  TITLE"
_SECTION_RE = re.compile(
    r"SECTION\s+([\d][\d\s]{4,10})\s*[-]?\s*([A-Z][A-Z &/,\-]+)",
    re.IGNORECASE,
)

# Matches clause numbers at the start of a line: "1.1", "2.3.1", "3.4.2.1"
_CLAUSE_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\s", re.MULTILINE)

EMBED_BATCH_SIZE = 100
CHROMA_BATCH_SIZE = 500  # ChromaDB Cloud max batch size is 1000; use 500 to stay safe

# text-embedding-3-small hard limit is 8192 tokens (~32k chars at 4 chars/token).
# Truncate at 28000 chars to stay safely under that limit.
MAX_EMBED_CHARS = 28000
# Clause chunks larger than this are split into sub-chunks to keep them embeddable.
MAX_CLAUSE_CHARS = 6000


# Local ChromaDB path for experiments (unlimited, no quota, fast re-indexing)
# Production uses ChromaDB Cloud — see scripts/setup_chromadb.py
CHROMA_LOCAL_PATH = "data/chromadb"


# -- ChromaDB -----------------------------------------------------------------

def _get_collection(local: bool = True, collection_name: str = COLLECTION_NAME) -> chromadb.Collection:
    if local:
        client = chromadb.PersistentClient(path=CHROMA_LOCAL_PATH)
    else:
        client = chromadb.HttpClient(
            host="api.trychroma.com",
            ssl=True,
            tenant=os.environ["CHROMA_TENANT"],
            database=os.environ["CHROMA_DATABASE"],
            headers={"x-chroma-token": os.environ["CHROMA_API_KEY"]},
        )
    return client.get_or_create_collection(name=collection_name)


# -- PDF extraction -----------------------------------------------------------

def _extract_pages(pdf_path: Path) -> list[dict]:
    """Return list of {page: int, text: str} for non-empty pages."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for i in range(len(doc)):
        text = doc[i].get_text("text").strip()
        if text:
            pages.append({"page": i + 1, "text": text})
    doc.close()
    return pages


def _detect_section(text: str) -> str:
    """Try to extract CSI section number from the first page text."""
    match = _SECTION_RE.search(text)
    if match:
        return match.group(1).strip().replace(" ", "")
    return "unknown"


# -- Chunking strategies ------------------------------------------------------

def _chunk_fixed(pages: list[dict], chunk_size: int, overlap: int) -> list[dict]:
    """Split full text into fixed character-size overlapping chunks."""
    full_text = "\n".join(p["text"] for p in pages)
    chunks = []
    start = 0
    idx = 0
    while start < len(full_text):
        text = full_text[start : start + chunk_size].strip()
        if text:
            chunks.append({"text": text, "chunk_idx": idx, "clause": ""})
        start += chunk_size - overlap
        idx += 1
    return chunks


def _chunk_by_clause(pages: list[dict]) -> list[dict]:
    """Split at clause number boundaries (e.g. 1.1, 2.3.1)."""
    full_text = "\n".join(p["text"] for p in pages)
    positions = [(m.start(), m.group(1)) for m in _CLAUSE_RE.finditer(full_text)]

    if not positions:
        # Fall back to fixed chunking if no clause markers detected
        return _chunk_fixed(pages, chunk_size=1500, overlap=100)

    chunks = []
    for idx, (start, clause_num) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(full_text)
        text = full_text[start:end].strip()
        if len(text) <= 80:  # skip tiny fragments
            continue
        if len(text) <= MAX_CLAUSE_CHARS:
            chunks.append({"text": text, "chunk_idx": idx, "clause": clause_num})
        else:
            # Clause is too long — split into overlapping sub-chunks
            sub_start = 0
            sub_idx = 0
            while sub_start < len(text):
                sub_text = text[sub_start : sub_start + MAX_CLAUSE_CHARS].strip()
                if sub_text:
                    chunks.append({
                        "text": sub_text,
                        "chunk_idx": idx * 10000 + sub_idx,
                        "clause": clause_num,
                    })
                sub_start += MAX_CLAUSE_CHARS - 200  # 200-char overlap between sub-chunks
                sub_idx += 1

    return chunks


# -- Embedding ----------------------------------------------------------------

def _embed(texts: list[str], client: OpenAI) -> list[list[float]]:
    """Embed texts in batches; returns one embedding per text."""
    # Truncate as a hard safety net — _chunk_by_clause should prevent this in practice
    safe_texts = [t[:MAX_EMBED_CHARS] for t in texts]
    embeddings: list[list[float]] = []
    for i in range(0, len(safe_texts), EMBED_BATCH_SIZE):
        batch = safe_texts[i : i + EMBED_BATCH_SIZE]
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=batch,
        )
        embeddings.extend([r.embedding for r in response.data])
        print(f"  Embedded {min(i + EMBED_BATCH_SIZE, len(safe_texts))}/{len(safe_texts)} chunks")
    return embeddings


# -- ID generation ------------------------------------------------------------

def _chunk_id(network: str, stem: str, chunk_idx: int) -> str:
    key = f"{network}_{stem}_{chunk_idx}"
    return hashlib.md5(key.encode()).hexdigest()


# -- Main loader --------------------------------------------------------------

def load_adm_specs(
    chunking_strategy: Literal["fixed", "clause"] = "fixed",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    overwrite: bool = False,
    local: bool = True,
    collection_name: str = COLLECTION_NAME,
) -> dict[str, int]:
    """
    Load all ADM spec PDFs into a ChromaDB collection.

    Args:
        chunking_strategy: "fixed" (exp01 baseline) or "clause" (exp02+)
        chunk_size:        Characters per chunk — fixed strategy only
        chunk_overlap:     Overlap between chunks — fixed strategy only
        overwrite:         Delete existing chunks for each network before loading
        local:             Use local PersistentClient (True) or ChromaDB Cloud (False)
        collection_name:   Target collection — use different names per experiment

    Returns:
        {network: chunks_added}
    """
    pdf_files = sorted(SPECS_DIR.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in {SPECS_DIR.resolve()}")

    collection = _get_collection(local=local, collection_name=collection_name)
    openai_client = OpenAI()
    results: dict[str, int] = {}

    for pdf_path in pdf_files:
        network = NETWORK_MAP.get(pdf_path.stem.lower(), pdf_path.stem.lower())
        print(f"\n-- {network.upper()} ({pdf_path.name}) --")

        if overwrite:
            existing = collection.get(where={"network": network})
            if existing["ids"]:
                collection.delete(ids=existing["ids"])
                print(f"  Removed {len(existing['ids'])} existing chunks")

        pages = _extract_pages(pdf_path)
        print(f"  Extracted {len(pages)} pages")

        if chunking_strategy == "fixed":
            chunks = _chunk_fixed(pages, chunk_size, chunk_overlap)
        else:
            chunks = _chunk_by_clause(pages)
        print(f"  {len(chunks)} chunks  (strategy={chunking_strategy})")

        section = _detect_section(pages[0]["text"] if pages else "")

        texts = [c["text"] for c in chunks]
        embeddings = _embed(texts, openai_client)

        ids = [_chunk_id(network, pdf_path.stem, c["chunk_idx"]) for c in chunks]
        metadatas = [
            {
                "authority": AUTHORITY,
                "network": network,
                "section": section,
                "clause": c["clause"],
                "chunk_idx": c["chunk_idx"],
                "source_file": pdf_path.name,
                "chunking_strategy": chunking_strategy,
            }
            for c in chunks
        ]

        for i in range(0, len(ids), CHROMA_BATCH_SIZE):
            collection.add(
                ids=ids[i : i + CHROMA_BATCH_SIZE],
                documents=texts[i : i + CHROMA_BATCH_SIZE],
                embeddings=embeddings[i : i + CHROMA_BATCH_SIZE],
                metadatas=metadatas[i : i + CHROMA_BATCH_SIZE],
            )
            print(f"  Stored {min(i + CHROMA_BATCH_SIZE, len(ids))}/{len(ids)} chunks")

        results[network] = len(chunks)
        print(f"  Done -> {COLLECTION_NAME} [{network}]")

    total = collection.count()
    print(f"\nCollection total: {total} documents")
    return results


if __name__ == "__main__":
    print("Loading ADM specs (fixed chunking, 500 chars) -- Experiment 01 baseline")
    totals = load_adm_specs(chunking_strategy="fixed", chunk_size=500, chunk_overlap=50)
    print("\n-- Summary --")
    for network, count in totals.items():
        print(f"  {network:<15} {count} chunks")
