from __future__ import annotations

from openai import OpenAI

from src.models.submittal import ClassifiedDocument
from src.parsers.pdf_parser import extract_text_from_bytes

EMBED_MODEL = "text-embedding-3-small"
MAX_EMBED_CHARS = 28_000
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def _chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end].strip())
        start = end - CHUNK_OVERLAP
    return [c for c in chunks if c]


def embed_submittal_documents(
    classified_docs: dict[str, ClassifiedDocument],
    file_contents: dict[str, bytes],
) -> list[tuple[str, str, list[float], dict]]:
    """
    Embed all submitted documents for post-review Q&A.
    Returns list of (chunk_id, text, embedding, metadata) tuples.
    """
    results = []
    for filename, doc in classified_docs.items():
        content = file_contents.get(filename)
        if content is None:
            continue
        full_text = extract_text_from_bytes(content)
        chunks = _chunk_text(full_text)
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            embedding = (
                _openai()
                .embeddings.create(model=EMBED_MODEL, input=[chunk[:MAX_EMBED_CHARS]])
                .data[0]
                .embedding
            )
            chunk_id = f"{filename}_c{i:04d}"
            metadata = {
                "filename": filename,
                "doc_type": doc.doc_type.value,
                "chunk_index": i,
            }
            results.append((chunk_id, chunk, embedding, metadata))
    return results
