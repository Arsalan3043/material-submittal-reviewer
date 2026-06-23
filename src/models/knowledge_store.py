from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from src.config.paths import STORE_DIR
from src.models.submittal import DocType

_STORE_DIR = STORE_DIR

# Module-level cache: each store file is read from disk only once per process,
# regardless of how many agents call load_store() in the same LangGraph run.
_cache: dict[str, SubmittalKnowledgeStore] = {}


class DocumentSection(BaseModel):
    """
    One classified document section within a submittal.

    For individual-file uploads: one section = one uploaded PDF.
    For bundled-PDF uploads: one section = a page range extracted from the
    single large PDF, bounded by the next separator page or section boundary.
    """
    doc_type: DocType
    text: str                           # full extracted text for this section
    pages: list[int]                    # 1-indexed page numbers covered
    confidence: str                     # "high" | "medium"
    filename: str                       # original filename or "bundled[type:pN]"
    declared_label: str | None = None   # section label provided at upload (if any)
    mismatch_flagged: bool = False      # True when declared label ≠ actual doc type


class SubmittalKnowledgeStore(BaseModel):
    """
    All knowledge extracted from a single material submittal.

    Built ONCE by doc_processor (Knowledge Builder), written to disk, then
    consumed by every downstream agent via load_store(). No PDF bytes live
    here — only extracted text and structured data. LangGraph state carries
    only the file path (knowledge_store_id), keeping state tiny.
    """
    submittal_id: str
    authority: str

    # ── Cover page metadata (extracted by LLM from cover page text) ────────
    material_description: str = ""
    spec_clause: str = ""
    manufacturer_name: str = ""
    manufacturer_address: str = ""
    supplier_name: str = ""
    supplier_address: str = ""

    # ── Classified sections (text only, no bytes) ─────────────────────────
    # Only sections with confidence "high" or "medium" are stored.
    # Multiple sections of the same doc_type are allowed (e.g., two test reports).
    sections: list[DocumentSection] = []

    # ── Pre-parsed comparison table rows ──────────────────────────────────
    # doc_processor runs table_extractor (pdfplumber + LLM) upfront so that
    # table_auditor never needs PDF bytes — it reads structured rows directly.
    # Stored as list[TableRow.model_dump()] to stay JSON-serialisable.
    table_rows: list[dict] = []

    # ── Query helpers ──────────────────────────────────────────────────────

    def get_text(self, doc_type: DocType) -> str:
        """Concatenated text for all sections of a given type."""
        return "\n\n".join(
            s.text for s in self.sections
            if s.doc_type == doc_type and s.text.strip()
        )

    def has_type(self, doc_type: DocType) -> bool:
        """Return True if at least one section of this type is present."""
        return any(s.doc_type == doc_type for s in self.sections)

    def get_present_types(self) -> set[DocType]:
        """Set of all doc types found in this submittal."""
        return {s.doc_type for s in self.sections}

    def get_mismatches(self) -> list[DocumentSection]:
        """Sections where the declared upload label did not match the actual type."""
        return [s for s in self.sections if s.mismatch_flagged]

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self) -> str:
        """
        Write this store to data/knowledge_stores/{submittal_id}.json.
        Returns the file path string, which becomes knowledge_store_id in state.
        Also populates the module-level cache so the same process never re-reads.
        """
        _STORE_DIR.mkdir(parents=True, exist_ok=True)
        path = _STORE_DIR / f"{self.submittal_id}.json"
        path.write_text(self.model_dump_json(indent=2))
        _cache[str(path)] = self
        return str(path)


def load_store(knowledge_store_id: str) -> SubmittalKnowledgeStore:
    """
    Load a SubmittalKnowledgeStore by its file path.
    Cached: disk is read at most once per (knowledge_store_id, process lifetime).
    """
    if knowledge_store_id in _cache:
        return _cache[knowledge_store_id]
    store = SubmittalKnowledgeStore.model_validate_json(
        Path(knowledge_store_id).read_text()
    )
    _cache[knowledge_store_id] = store
    return store
