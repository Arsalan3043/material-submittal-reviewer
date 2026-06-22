from __future__ import annotations

from langsmith.wrappers import wrap_openai
from openai import OpenAI

from src.models.submittal import ClassifiedDocument, DocType
from src.parsers.pdf_parser import extract_text_from_bytes

EMBED_MODEL = "text-embedding-3-small"
MAX_EMBED_CHARS = 28_000
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBED_BATCH_SIZE = 100  # OpenAI supports up to 2048; 100 is safe and practical

# Document types with no useful text content for Q&A — skip embedding entirely.
# BOQ is a quantity table, drawings extract near-zero text.
_SKIP_DOC_TYPES = frozenset([DocType.BOQ, DocType.DRAWING])

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = wrap_openai(OpenAI())
    return _client


def _chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end].strip())
        start = end - CHUNK_OVERLAP
    return [c for c in chunks if c]


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in a single API call. Returns embeddings in the same order."""
    response = _openai().embeddings.create(
        model=EMBED_MODEL,
        input=[t[:MAX_EMBED_CHARS] for t in texts],
    )
    # API guarantees results are returned in the same order as input
    return [item.embedding for item in response.data]


def embed_submittal_documents(
    classified_docs: dict[str, ClassifiedDocument],
    file_contents: dict[str, bytes],
) -> list[tuple[str, str, list[float], dict]]:
    """
    Embed all submitted documents for post-review Q&A.
    Returns list of (chunk_id, text, embedding, metadata) tuples.

    Skips BOQ and drawing documents (no useful text content).
    Batches embedding API calls at EMBED_BATCH_SIZE to reduce API round-trips
    (was: 1 API call per chunk → now: 1 call per EMBED_BATCH_SIZE chunks).
    """
    # Step 1: collect all chunks and their metadata first
    pending: list[tuple[str, str, dict]] = []  # (chunk_id, text, metadata)

    for filename, doc in classified_docs.items():
        if doc.doc_type in _SKIP_DOC_TYPES:
            continue
        content = file_contents.get(filename)
        if content is None:
            continue
        full_text = extract_text_from_bytes(content)
        if not full_text.strip():
            continue
        chunks = _chunk_text(full_text)
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            chunk_id = f"{filename}_c{i:04d}"
            metadata = {
                "filename": filename,
                "doc_type": doc.doc_type.value,
                "chunk_index": i,
            }
            pending.append((chunk_id, chunk, metadata))

    if not pending:
        return []

    # Step 2: embed in batches — N chunks → ceil(N / EMBED_BATCH_SIZE) API calls
    all_embeddings: list[list[float]] = []
    texts = [p[1] for p in pending]

    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        all_embeddings.extend(_embed_batch(batch))

    # Step 3: zip chunks with their embeddings
    return [
        (chunk_id, text, embedding, metadata)
        for (chunk_id, text, metadata), embedding in zip(pending, all_embeddings)
    ]
