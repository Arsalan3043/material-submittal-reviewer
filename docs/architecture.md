# Architecture — Material Submittal Reviewer
> Deep reference for understanding how every component works, what data flows where,
> and why decisions were made. Intended for debugging and extending the system.
> Read the code alongside this — this document explains the *why* and *how*, the code shows the *what*.

---

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Data Models — The Shared Language](#2-data-models--the-shared-language)
3. [PDF Parsing Layer](#3-pdf-parsing-layer)
4. [Document Classifier](#4-document-classifier)
5. [RAG Indexing Pipeline](#5-rag-indexing-pipeline)
6. [RAG Query Pipeline](#6-rag-query-pipeline)
7. [Path Configuration (src/config/paths.py)](#7-path-configuration-srcconfigpathspy)
8. [File I/O Layer (src/parsers/file_io.py)](#8-file-io-layer-srcparsersfle_iopy)
9. [LangGraph Orchestrator](#9-langgraph-orchestrator)
10. [Agent 1 — Document Processor](#10-agent-1--document-processor)
11. [Agent 2 — Spec Verifier](#11-agent-2--spec-verifier)
12. [Agent 3 — Validity Checker (Rule-Based)](#12-agent-3--validity-checker-rule-based)
13. [Agent 4 — Table Auditor](#13-agent-4--table-auditor)
14. [Agent 5 — Consistency Checker (Rule-Based)](#14-agent-5--consistency-checker-rule-based)
15. [Agent 6 — AVL Checker (TAQA Only)](#15-agent-6--avl-checker-taqa-only)
16. [Agent 7 — Report Compiler](#16-agent-7--report-compiler)
17. [Agent 8 — Query Agent (Post-Review Chat)](#17-agent-8--query-agent-post-review-chat)
18. [Rule-Based Components](#18-rule-based-components)
19. [Authority Profiles](#19-authority-profiles)
20. [Streamlit UI Layer](#20-streamlit-ui-layer)
21. [End-to-End Data Flow](#21-end-to-end-data-flow)
22. [ChromaDB Storage Structure](#22-chromadb-storage-structure)
23. [Common Failure Modes and How to Diagnose Them](#23-common-failure-modes-and-how-to-diagnose-them)

---

## 1. System Overview

The system reviews UAE construction material submittals — packages of documents a contractor submits when proposing a material. The review process has 9 stages and must always complete all stages regardless of what is found.

**Three pipelines run the system:**

```
PIPELINE 1 — INDEXING (runs once, admin action)
  Authority spec PDFs → parse → chunk → embed → store in ChromaDB
  Collection: adm_specifications (or taqa_specifications)

PIPELINE 2 — QUERY (runs during every review)
  Spec clause reference → build query → BM25 + semantic → RRF → rerank → parent fetch
  Returns: relevant spec text for that clause

PIPELINE 3 — REVIEW (runs on every submittal upload)
  PDFs uploaded → LangGraph state machine → 8 agents → report + query mode
```

The key insight: Pipeline 1 only runs when specs change. Pipelines 2 and 3 run on every review. Pipeline 2 is called from inside Pipeline 3 (agents call it during review).

**LLM choice:** Only GPT-4o-mini is used for all LLM calls (not GPT-4o). This was proven in Phase 2 Experiment A to achieve 96.2% effective classification accuracy at a fraction of the cost.

---

## 2. Data Models — The Shared Language

Every component speaks through Pydantic models. Understanding these first is essential.

### `DocType` (enum — `src/models/submittal.py`)
The 14 document types the classifier can assign:
```
cover_page, msdf, specification_copy, boq, drawing, comparison_table,
technical_datasheet, test_report, ded_registration, manufacturer_guarantee,
previous_approval, method_statement, maf, others
```
`others` is the catch-all for anything unrecognized.

### `SubmittalMetadata` (`src/models/submittal.py`)
Created at upload time in the Streamlit UI. Stored in `st.session_state` for the duration of a review session.

```python
class SubmittalMetadata(BaseModel):
    submittal_id: str       # plain UUID — opaque identifier, no user scoping baked in
    authority: str          # "ADM" or "TAQA"
    project_name: str       # optional, entered by user at upload
    material_description: str
    created_at: str         # ISO datetime string
    # user_id: str | None = None    ← add when multi-tenancy is needed
    # tenant_id: str | None = None  ← add when multi-tenancy is needed
```

The `submittal_id` is always a plain UUID. Scoping (user, tenant) lives as explicit fields, not embedded in the key. This means the ID is stable across auth changes and the key never needs migrating when the access control model changes. See `engineering_log.md` Issue 5.3 for the full debate.

### `ClassifiedDocument` (`src/models/submittal.py`)
Intermediate output from the classifier for a single file. Used internally by `doc_processor` to detect mismatches before building the knowledge store. Not stored in LangGraph state — only the knowledge store survives past Agent 1.

- `filename` — original filename (or virtual name like `file.pdf[cover_page:p1]` for bundled PDFs)
- `doc_type` — one of the 14 DocType values
- `confidence` — "high" | "medium" | "low"
- `reasoning` — one-sentence explanation from the LLM
- `key_indicators` — text fragments that led to the classification
- `text_preview` — first 500 chars; used only by classifier's mismatch detection
- `mismatch_flagged` — True if the document was uploaded under the wrong index section

### `DocumentSection` (`src/models/knowledge_store.py`)
One classified document section inside the `SubmittalKnowledgeStore`. Holds the **full extracted text** (not a preview) for that section:

```python
class DocumentSection(BaseModel):
    doc_type: DocType
    text: str               # full extracted text — not truncated
    pages: list[int]        # 1-indexed page numbers covered
    confidence: str         # "high" | "medium"
    filename: str
    declared_label: str | None
    mismatch_flagged: bool
```

For individual-file uploads: one `DocumentSection` per uploaded PDF.
For bundled-PDF uploads: one `DocumentSection` per identified section within the large PDF.

### `SubmittalKnowledgeStore` (`src/models/knowledge_store.py`)
The central knowledge object built once by Agent 1 (doc_processor) and read by every downstream agent. State carries only the file path string (`knowledge_store_id`, ~100 chars). No PDF bytes or large dicts live in LangGraph state.

```python
class SubmittalKnowledgeStore(BaseModel):
    submittal_id: str
    authority: str

    # Extracted from cover page by LLM:
    material_description: str
    spec_clause: str
    manufacturer_name: str
    manufacturer_address: str
    supplier_name: str
    supplier_address: str

    # All classified sections (text only):
    sections: list[DocumentSection]

    # Pre-parsed comparison table rows (list[TableRow.model_dump()]):
    # doc_processor runs table extraction upfront so table_auditor never needs PDF bytes.
    table_rows: list[dict]
```

**Key methods:**
- `store.get_text(DocType.TECHNICAL_DATASHEET)` — concatenated text for all sections of a given type
- `store.has_type(DocType.TEST_REPORT)` — True if at least one section of this type exists
- `store.get_present_types()` — set of all DocTypes found
- `store.get_mismatches()` — sections where declared label ≠ actual type
- `store.save()` — writes to `data/knowledge_stores/{submittal_id}.json`, returns path string
- `load_store(path)` — loads from disk; module-level cache means disk is read at most once per process

### `SubmittalReviewState` (TypedDict — `src/agents/state.py`)
The shared state object that flows between all LangGraph nodes. TypedDict (not Pydantic) for LangGraph compatibility. Every Pydantic object stored here is serialized via `.model_dump()` and reconstructed at the point of use via `Model.model_validate()`.

**PDF bytes are never in state.** They are deposited in a staging dict before `graph.invoke()` and consumed immediately by the first node. See Section 10 (Agent 1) for the staging pattern.

Key fields and who writes them:
```
authority              → Input (set before graph.invoke())
submittal_id           → Input (set before graph.invoke())
review_date            → Input (defaults to today)
knowledge_store_id     → Agent 1 (doc_processor) — file path to SubmittalKnowledgeStore JSON
completeness_findings  → completeness node (inline, between Agent 1 and Agent 2)
spec_verif_findings    → Agent 2 (spec_verifier)
validity_findings      → Agent 3 (validity_checker)
table_audit_findings   → Agent 4 (table_auditor)
consistency_findings   → Agent 5 (consistency_checker)
avl_findings           → Agent 6 (avl_checker) or skip_avl node
report                 → Agent 7 (report_compiler)
conversation_history   → handle_query() wrapper (post-review)
missing_documents      → completeness node
review_complete        → Agent 7 (report_compiler)
```

All cover page fields (`material_description`, `spec_clause`, `manufacturer_name`, etc.) live in `SubmittalKnowledgeStore`, not in state. Agents that need them call `load_store(state["knowledge_store_id"]).spec_clause` etc.

### `Finding` (`src/models/findings.py`)
The universal finding format for all non-table stages:
- `stage` — which stage produced it ("completeness_check", "spec_verification", etc.)
- `document` — which file it relates to
- `description` — what was found
- `severity` — PASS | WARNING | CRITICAL
- `action_required` — what the contractor must do

### `TableRowFinding` (`src/models/findings.py`)
Extended format for comparison table rows:
- All columns from the extracted table (parameter, specified_value, proposed_value, etc.)
- Boolean audit flags (specified_correct, proposed_verified, measured_verified, deviation_accurate, missing_from_spec)
- `finding` — one-sentence summary
- `severity`

### `ReviewReport` (`src/models/findings.py`)
The final report. Contains all nine lists of findings + computed counts + recommendation + summary. The `critical_count` and `warning_count` fields are computed automatically by a `@model_validator` when the report is created — they are NOT computed manually.

Recommendation logic (in report_compiler.py):
```
critical_count > 0         → RESUBMIT
warning_count > 2          → CONDITIONAL
otherwise                  → APPROVE
```

---

## 3. PDF Parsing Layer

**File:** `src/parsers/pdf_parser.py`

The fundamental challenge: most real UAE submittals are scanned PDFs, not digital. Scanned PDFs have zero extractable text from PyMuPDF — every character is an image pixel.

### How text extraction actually works

Every page goes through this exact logic:

```python
# 1. Try native text extraction (PyMuPDF)
native = page.get_text().strip()

# 2. Count characters
if len(native) >= 50:   # _MIN_NATIVE_CHARS = 50
    return native        # Digital PDF — use native text

# 3. If fewer than 50 chars → treat as scanned, run OCR
pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))  # 2× zoom = ~144 DPI
img = PIL.Image.open(BytesIO(pix.tobytes("png")))
ocr_text = pytesseract.image_to_string(img)
return ocr_text if ocr_text else native
```

**Why 2× zoom?** Tesseract accuracy degrades at the native PDF resolution (~72 DPI). 2× gives ~144 DPI which Tesseract handles well. This was proven in Phase 2 Experiment A.

**Why 50 chars?** Scanned PDFs often have a few characters from embedded fonts or metadata — the 50-char threshold filters those out while catching all real digital text. A page with 3 chars of metadata and 200 chars of actual text would pass native; that's fine.

### `is_separator_page(text, max_words=60)`
Detects UAE routing slip pages (the divider pages between sections in a bundled PDF). These pages typically contain only:
- "Authority | Employer | Engineer | Contractor" routing columns
- Very few words (7-20 typical)

The detection checks: word count ≤ 60 AND the four UAE routing keywords are all present. If that fails, falls back to pattern matching for "cover page", "index N", "tab N", etc.

---

## 4. Document Classifier

**File:** `src/parsers/classifier.py`

### The core call
Every classification is one GPT-4o-mini call with a structured system prompt defining all 14 document types with their distinguishing features. The model returns JSON:
```json
{
  "doc_type": "cover_page",
  "confidence": "high",
  "reasoning": "Has EMPLOYER/PMC/ENGINEER/CONTRACTOR routing columns and enclosures checklist",
  "key_indicators": ["EMPLOYER", "NEW SUBMITTAL", "Enclosures checklist"]
}
```

### Critical classifier rules (hard-won from Experiment A)
The cover_page vs maf distinction is the hardest case. A contractor's transmittal form (cover page) looks similar to an official authority MAF form. The classifier has explicit rules:
- If routing columns (EMPLOYER/PMC/ENGINEER/CONTRACTOR) OR enclosures checklist → `cover_page`, not `maf`
- MAF requires ALL of: official government authority name/logo, bilingual Arabic/English, authority-specific stamps, approval status options
- A status section on a cover page does NOT make it a MAF

### `classify_uploaded_file()` — the full pipeline
1. Extract text from first 2 pages (max_pages=2 — covers are usually 1-2 pages)
2. Call `classify_document(text, declared_label)` → GPT-4o-mini → `_ClassificationResult`
3. Check for section mismatch:
   - Was this file uploaded under a section label (e.g. "BOQ & Drawings")?
   - Does the actual `doc_type` match the expected type for that label?
   - If not → set `mismatch_flagged = True`
   - Exception: `maf` in "Previous Approvals" section is NOT a mismatch (known UAE convention)
4. Return `ClassifiedDocument`

### `_LABEL_TO_DOCTYPE` mapping
The mismatch detection map:
```python
"BOQ & Drawings"                          → DocType.BOQ
"Copies of Relevant Specifications"       → DocType.SPECIFICATION_COPY
"Technical Comparison Table"              → DocType.COMPARISON_TABLE
"Manufacturer's Technical Data"           → DocType.TECHNICAL_DATASHEET
"Recent Test Reports and Certificates"    → DocType.TEST_REPORT
"Department of Economic Development..."   → DocType.DED_REGISTRATION
"Manufacturer/Supplier Guarantee"         → DocType.MANUFACTURER_GUARANTEE
"Previous Approvals"                      → DocType.PREVIOUS_APPROVAL
"Applicator's Method Statement"           → DocType.METHOD_STATEMENT
"Material Approval Form"                  → DocType.MAF
"Material Source Declaration Form"        → DocType.MSDF
```

---

## 5. RAG Indexing Pipeline

**Files:** `src/rag/indexing/`

This pipeline runs once (admin action) to load authority spec PDFs into ChromaDB. It does NOT run during reviews.

### Step 1 — PDF Loader (`pdf_loader.py`)
Reads every page of the spec PDF. Returns a list of `RawSpecPage` objects, each with: `authority`, `network`, `source_file`, `page_num`, `text`.

`network` is the spec book label (e.g. "irrigation", "road", "waterproofing"). It is the primary metadata filter key — without it, BM25 and semantic search would mix results from different spec books.

### Step 2 — Structurer (`structurer.py`)
Groups raw pages into `SpecSection` objects by clause boundary. The algorithm:

```
For each page:
  Look for a clause number pattern: e.g. "03300", "33 40 00", "03300.2.3"
  If new clause found → save current section, start new section
  If no new clause → append page text to current section
```

This means each `SpecSection` = one spec clause + all its sub-clauses. Some clauses span many pages; others are one paragraph.

The regex `r"^(\d{2,6}(?:\s\d{2}\s\d{2})?(?:\.\d+)*)\s+\w"` matches both the legacy 5-digit format (03300) and the newer space-separated format (33 40 00).

### Step 3 — Chunker (`chunker.py`)
Splits each `SpecSection` into fixed 500-char chunks with 50-char overlap.

**Why fixed chunks over clause-boundary chunks?** Experiment 2 tested clause-boundary chunking and found it collapsed context_precision (from 0.86 to 0.31). The problem: large clauses produce diffuse embeddings that match everything weakly. Fixed 500-char chunks produce tight embeddings that match precisely.

**Oversized clause handling:** Some spec clauses are 18,000+ characters. The embedding API has a ~28,000 char limit. For safety, sections longer than 6,000 chars are first sub-split into 6,000-char pieces with 200-char overlap, then each sub-piece is fixed-chunked. This is the `MAX_CLAUSE_CHARS = 6000` guard.

Each chunk carries:
- `chunk_id` — unique string (`ADM_irrigation_00042_c0003`)
- `parent_id` — the section this chunk belongs to (`ADM_irrigation_00042`)
- All metadata fields for filtering

### Step 4 — Metadata Tagger (`metadata_tagger.py`)
Converts each `SpecChunk` into a dict with 8 metadata fields stored alongside the chunk in ChromaDB:
```python
{
    "authority":   "ADM",
    "network":     "irrigation",    # ← PRIMARY FILTER KEY
    "division":    "33",
    "section":     "33 10",
    "clause":      "33 10 13",
    "source_file": "ADM_Irrigation_Spec.pdf",
    "chunk_index": 3,
    "parent_id":   "ADM_irrigation_00042",
}
```

### Step 5 — Indexer (`indexer.py`)
Orchestrates the full pipeline. Embeds chunks in batches of 500 (ChromaDB add() limit is 1,000 — 500 gives safe margin). Calls `text-embedding-3-small` via OpenAI API. Stores `(id, document, embedding, metadata)` tuples in ChromaDB.

Uses `chromadb.PersistentClient(path=CHROMA_PATH)` where `CHROMA_PATH` is imported from `src/config/paths.py`. All ChromaDB data is stored locally on disk — see Section 7 for the path configuration.

---

## 6. RAG Query Pipeline

**Files:** `src/rag/query/`

This pipeline runs during every review — called from `context_assembler.py` which is called from both `spec_verifier.py` and `table_auditor.py`.

### The full retrieval flow

```
spec_clause (e.g. "33 10 13") + authority ("ADM")
        ↓
query_constructor.py → StructuredQuery
  - question: "What are the technical requirements for X per specification clause 33 10 13?"
  - network: "irrigation"   (looked up from _CLAUSE_TO_NETWORK map)
  - metadata_filter: {"network": "irrigation"}
        ↓
hybrid_retriever.py → retrieves top-20 candidates
  ├── BM25 path:
  │     Load all chunks from ChromaDB where network="irrigation"
  │     Build BM25Okapi index (cached per (collection, network) pair — only built once)
  │     Score query tokens against all chunks
  │     Take top-20 by BM25 score
  │
  └── Semantic path:
        Embed the question with text-embedding-3-small
        Query ChromaDB with where={"network": "irrigation"}
        Take top-20 by cosine similarity
        ↓
  RRF fusion: combine both ranked lists
  Score = 1/(60 + rank_in_semantic_list) + 1/(60 + rank_in_bm25_list)
  Sort by RRF score, keep top-20
        ↓
reranker.py → top-20 → top-5
  Cohere rerank-english-v3.0 cross-encoder
  Re-scores each chunk against the question (not just keyword/vector match)
  Returns top-5 most relevant chunks
        ↓
parent_fetcher.py → fetch full clause context
  For each of the top-5 chunks:
    Look up its parent_id in ChromaDB metadata
    Fetch ALL chunks with that parent_id
    Sort by chunk_index → reassemble full clause text
  Returns full clause texts (much more context than the 500-char fragments)
        ↓
context_assembler.py → format for LLM
  Joins all texts as:
  "[Context 1]\n<text>\n\n---\n\n[Context 2]\n<text>..."
```

### Why BM25 is filtered by network before building
Experiment 3 (hybrid search without network filter) collapsed `context_precision` from 0.86 to 0.51. The reason: BM25 was matching clauses from irrigation specs when querying waterproofing specs (shared technical terms). Filtering BM25 to the correct network first eliminated the cross-spec noise completely.

### Why RRF uses K=60
RRF_K=60 is the standard constant from the original RRF paper. It controls the weight given to rank position. Lower K → rank 1 gets much more weight than rank 2. K=60 gives a smooth distribution that works well when both BM25 and semantic are reliable.

### The `lru_cache` in context_assembler.py
Both `spec_verifier` and `table_auditor` call `assemble_spec_context(clause_ref, authority)` for the same clause in the same review. Without the cache, that's 2× embedding calls + 2× Cohere calls per review. The `@lru_cache(maxsize=64)` on `_fetch_spec_context` means the second call is a free dict lookup. Cache key is `(clause_ref, authority)`.

### The `EMPTY_CONTEXT_SENTINEL`
If retrieval returns no candidates (clause not in ChromaDB, collection doesn't exist, network filter eliminates everything), the function returns the string `"__SPEC_NOT_FOUND__"`. Agents check for this string and generate a "spec not found" warning instead of hallucinating. This is a deliberate design — never hallucinate spec content.

---

## 7. Path Configuration (`src/config/paths.py`)

All local file paths are centralized in one module. Every other module imports from here — no hardcoded path strings elsewhere.

```python
PROJECT_ROOT: Path = Path(__file__).parent.parent.parent
DATA_DIR:        Path = PROJECT_ROOT / "data"
CHROMA_PATH:     str  = str(DATA_DIR / "chromadb")
STORE_DIR:       Path = DATA_DIR / "knowledge_stores"
SPECS_DIR:       Path = DATA_DIR / "specs"
SUBMITTALS_DIR:  Path = DATA_DIR / "submittals"

def ensure_dirs() -> None:
    """Create all data subdirectories if they don't exist yet."""
    for d in (STORE_DIR, SPECS_DIR, SUBMITTALS_DIR):
        d.mkdir(parents=True, exist_ok=True)
```

`PROJECT_ROOT` is derived from `__file__` (the location of `paths.py` itself), not from `os.getcwd()`. This makes all paths absolute and correct regardless of what directory the process was started from. Streamlit, pytest, and scripts all resolve to the same directories.

`ensure_dirs()` is called once in `app/main.py` at startup so the `data/` subdirectories always exist before any agent tries to write to them.

**Why this matters:** Before centralization, `"data/chromadb"` was hardcoded as a relative string in three separate files. Relative paths are fragile in web applications because the CWD depends on how the process was started. With absolute paths from `__file__`, the same code works from any launch point. See `engineering_log.md` Issue 3.4.

**Production transition:** To switch storage backends (S3, Azure Blob, ChromaDB Cloud), change the constants in this one file. Nothing else in the codebase needs to change.

---

## 8. File I/O Layer (`src/parsers/file_io.py`)

Single choke-point for all file reads and writes. Two functions cover all cases:

```python
def load_pdf_bytes(path: str | Path) -> bytes:
    """Read a PDF from local disk. In production, swap body to S3 download."""
    return Path(path).read_bytes()

def save_upload(dest_dir: str | Path, filename: str, data: bytes) -> Path:
    """Save bytes from a Streamlit file_uploader to disk. Returns absolute path."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / filename
    out.write_bytes(data)
    return out
```

`save_upload()` is called from the Streamlit UI (`upload.py` and `spec_manager.py`) to convert in-memory upload bytes into disk files before the pipeline runs. `load_pdf_bytes()` is called anywhere the pipeline needs to read a saved file.

**Production transition:** The production version of `load_pdf_bytes` downloads from S3 instead of reading from disk — one function body to change. `save_upload()` would upload to S3 and return an S3 key. No agent or orchestrator code changes.

---

## 9. LangGraph Orchestrator

**File:** `src/agents/orchestrator.py`

LangGraph models the review pipeline as a directed graph where each node is a function that takes `SubmittalReviewState` and returns an updated `SubmittalReviewState`.

### The graph structure

```
[doc_processor] → [completeness] → [boq_drawing] → [spec_verifier] → [validity_checker]
                                                                              ↓
                                                          ┌───────────────────┤
                                                          ↓ (if TAQA)         ↓ (if ADM)
                                                       [avl_check]       [skip_avl]
                                                          └──────────┬────────┘
                                                                     ↓
                                              [statement] → [table_auditor] → [consistency]
                                                                                    ↓
                                                                              [others] → [report_compiler] → END
```

### How LangGraph state updates work
Each node receives the full current state and returns a dict with only the fields it wants to update. The `{**state, "new_field": value}` pattern merges updates with existing state. LangGraph merges the returned dict into the state automatically.

**Critical:** Nodes do NOT modify state in place. They return a new dict. If a node forgets to pass through `{**state, ...}`, it wipes all state set by previous nodes.

### The conditional edge (TAQA vs ADM)
```python
def _should_run_avl(state) -> str:
    return "avl_check" if state.get("authority") == "TAQA" else "skip_avl"
```
`add_conditional_edges` passes this function to LangGraph. The function's return value is looked up in the edge map: `{"avl_check": "avl_check", "skip_avl": "skip_avl"}`. Both paths converge at `[statement]`.

### Placeholder nodes
Three nodes are stubs that return empty findings:
- `_boq_drawing_node` — BOQ/drawing material-type check (not yet implemented)
- `_statement_node` — compliance statement audit (not yet implemented)
- `_others_node` — OTHERS document review (not yet implemented)

These exist as graph nodes so the pipeline structure is complete — adding implementation later requires only filling in the function body, not changing the graph.

### The `completeness` node is inline (not a full agent)
The completeness check is defined directly in `orchestrator.py` as `_completeness_node`. It calls `check_completeness()` from `src/rules/completeness.py`. It is not a separate agent file because it is purely rule-based and has no dependencies on external services.

### Streaming to the UI
The Streamlit review page calls `graph.stream(initial_state)` rather than `graph.invoke()`. Each event from the stream is `{node_name: state_changes}`. The UI accumulates events with `accumulated.update(node_state)` to build the full final state, and displays a status line for each completed node in real time.

---

## 10. Agent 1 — Document Processor

**File:** `src/agents/doc_processor.py`

This is the first and most complex agent, renamed internally to "Knowledge Builder" because its primary output is the `SubmittalKnowledgeStore`, not just a classification result.

### The staging dict pattern
PDF bytes never enter LangGraph state. Before `graph.invoke()` is called, the UI calls:

```python
stage_files(submittal_id, file_contents, declared_labels)
```

This deposits bytes in a module-level dict `_staging`. The first thing `doc_processor_node` does is:

```python
file_contents, declared_labels = _staging.pop(submittal_id, ({}, {}))
```

Bytes are consumed here and discarded. Nothing is forwarded. LangGraph state carries only `knowledge_store_id` (a ~100-char string) after this node, not megabytes of binary data. See `engineering_log.md` Issue 3.1 for why this matters (LangSmith trace size, memory).

### What Agent 1 builds
Agent 1 constructs a `SubmittalKnowledgeStore` from the raw bytes:

1. **Classify and extract text** — for each file, run the classifier, then extract full text (not just 2-page preview)
2. **Build `DocumentSection` objects** — one per identified document, with full text
3. **Extract cover page metadata** — one GPT-4o-mini call to get `material_description`, `spec_clause`, `manufacturer_name`, `manufacturer_address`, `supplier_name`, `supplier_address`; written to `store.*`
4. **Pre-parse comparison table rows** — call `extract_all_table_rows()` on the comparison table PDF upfront; rows stored as `store.table_rows`; downstream `table_auditor` reads these directly without needing any PDF bytes
5. **Write to disk** — `store.save()` writes `data/knowledge_stores/{submittal_id}.json`
6. **Return** — `{**state, "knowledge_store_id": "/path/to/{id}.json"}`

This single agent is the only one that ever reads PDF bytes. All downstream agents call `load_store(state["knowledge_store_id"])` to get what they need.

### Format A: Individual files (multiple PDFs uploaded)
When more than one PDF is uploaded, or the single PDF has fewer than 20 pages:
1. For each file: extract text (max 2 pages for classification) → classify → build `ClassifiedDocument`
2. Then extract full text (`max_pages=None`) → build `DocumentSection` with full text
3. If `doc_type == COVER_PAGE`: use full text for cover page extraction (up to `_MAX_COVER_CHARS = 3000`)
4. If `doc_type == COMPARISON_TABLE`: keep bytes for upfront table row parsing
5. Append section to `store.sections`

### Format B: Bundled PDF (one large PDF containing the whole submittal)
When exactly one PDF is uploaded AND it has ≥ 20 pages (`_BUNDLED_THRESHOLD = 20`):

The bundled PDF problem: a single 80-page PDF contains 10+ different document types. The system must identify what's in it without classifying every page (that would be 80 LLM calls).

**Option C hybrid approach:**

**Step 1 — Separator scan (zero LLM cost):**
Scan every page using `is_separator_page()` (pure regex, no API calls). Count separator pages.

**If ≥ 2 separators found → Option A (separator-based splitting):**
- Build list of `section_starts` = pages immediately after each separator
- Classify only the first content page of each section (1 LLM call per section)
- Skip duplicate doc_types (once a COVER_PAGE is found, don't classify more COVER_PAGEs)
- Confidence filter: only keep "high" or "medium" confidence classifications
- Extract full section text forward until the next separator boundary

**If < 2 separators → Option B (sparse sampling):**
- Step through the document every 3 pages (`_SAMPLE_STEP = 3`)
- At each step, examine up to 3 consecutive pages (`_SAMPLE_WINDOW = 3`)
- Classify each page individually
- Skip duplicates (once COVER_PAGE seen, don't classify again)
- Early stop after 5 consecutive steps with no new doc type (`_EARLY_STOP_STEPS = 5`)

**Why step=3 window=3?** The window covers 3 pages at each step, which means sections as short as 1-2 pages are still sampled at least once. Step-3 ensures sections aren't skipped between steps.

### Virtual document naming
For sections found inside a bundled PDF, a virtual filename is created:
```
"submittal_package.pdf[cover_page:p1]"
"submittal_package.pdf[test_report:p23]"
```
This allows the rest of the system to treat them as if they were separate files.

---

## 11. Agent 2 — Spec Verifier

**File:** `src/agents/spec_verifier.py`

### What it does
Compares the submitted specification copy (Index 2 — what the contractor includes as their copy of the spec) against the actual authority spec stored in ChromaDB.

### Execution flow
1. Load knowledge store: `store = load_store(state["knowledge_store_id"])`
2. Read `store.spec_clause` — extracted from cover page by Agent 1
3. If empty → generate WARNING finding, return early
4. Call `assemble_spec_context(clause_ref=store.spec_clause, authority=authority)` → this triggers the full RAG pipeline (query → BM25+semantic → RRF → rerank → parent fetch)
5. If returns `EMPTY_CONTEXT_SENTINEL` → generate WARNING ("clause not in database"), return early
6. Get submitted spec text: `store.get_text(DocType.SPECIFICATION_COPY)` — returns full text, not a preview
7. If empty → generate CRITICAL finding ("missing Index 2"), return early
8. Send BOTH texts to GPT-4o-mini with prompt: "Find discrepancies between submitted spec and authority spec"
9. Parse the JSON response into `Finding` objects

### What the LLM looks for
The system prompt instructs the LLM to flag:
- `wrong_clause` — submitted clause number doesn't match what's on the cover page
- `incomplete_section` — submitted copy is cut off or missing sub-clauses
- `wrong_values` — submitted spec shows different numeric values than authority spec
- `correct` — no discrepancies found

Minor formatting differences are explicitly excluded. Only genuine content discrepancies get flagged.

---

## 12. Agent 3 — Validity Checker (Rule-Based)

**File:** `src/agents/validity_checker.py`
**Rules in:** `src/rules/date_checker.py`

No AI. Pure Python datetime logic.

### What it checks
Loads the knowledge store and iterates over all sections. For each one:
- `DED_REGISTRATION` → calls `check_ded_registration()`
- `TEST_REPORT` → calls `check_test_report()`
- `MANUFACTURER_GUARANTEE` → calls `check_guarantee()`

The full section text (`section.text`) is used — not a preview. Date extraction runs on the complete document content.

### Date extraction logic (`date_checker.py`)
Five regex patterns cover all common date formats in UAE construction docs:
- DD/MM/YYYY (most common in UAE)
- DD-MM-YYYY, YYYY-MM-DD
- "15 Jan 2024", "January 15, 2024"
- "Expiry: 15/01/2024" (labeled expiry)

For **DED registration**: First tries to find a labeled expiry date ("expiry:", "valid until:", etc.). If none found, uses the latest date in the document. Compares to today:
- Past → CRITICAL (expired)
- Within 30 days → WARNING (expiring soon)
- Future → no finding generated

For **test reports**: Uses the most recent date as the report date. Calculates age in days. Max age = 3 × 365 = 1,095 days (3 years). Over limit → CRITICAL.

For **guarantee**: Regex extracts period ("10 years", "5 yr", "24 months"). Converts to years. Compares against `required_years` parameter (not currently wired from spec — always None in current code, so only "no period found" warning is generated).

### What this agent cannot do
It cannot read the actual "Expiry Date" field from a certificate that uses an image-only format (scanned DED where OCR fails). In that case, dates list will be empty and a WARNING is generated asking for manual verification.

---

## 13. Agent 4 — Table Auditor

**File:** `src/agents/table_auditor.py`
**Table extraction in:** `src/parsers/table_extractor.py`

This is the highest-value agent. It audits every row of the comparison table.

### Important: table rows are pre-parsed by Agent 1
Agent 4 does NOT extract table rows from PDF bytes. This work is done upfront by Agent 1 (doc_processor) during its run — `extract_all_table_rows()` is called on the comparison table PDF and the result stored in `store.table_rows`. Agent 4 reads the pre-parsed rows:

```python
store = load_store(state["knowledge_store_id"])
table_rows = [TableRow.model_validate(r) for r in store.table_rows]
```

This design decision (doing extraction upfront in Agent 1) means the table auditor never needs PDF bytes and can focus entirely on the audit logic. See `engineering_log.md` Issue 3.2.

### How table extraction works (done in Agent 1)
For each page in the comparison table PDF (`src/parsers/table_extractor.py`):

**OCR-first approach** (because UAE submittals are scanned):
1. OCR the page with Tesseract at 2× zoom
2. If OCR returns ≥ 5 words → send to GPT-4o-mini for table parsing
3. If OCR returns < 5 words → try pdfplumber (fallback for digital PDFs)
4. If pdfplumber finds rows → send the pipe-delimited text to GPT-4o-mini

The LLM extracts rows into this structure:
```python
class TableRow:
    parameter: str    # e.g. "pH"
    specified: str    # e.g. "6.0 - 8.0"
    proposed: str     # e.g. "7.2"
    deviation: str    # often empty — means no declared deviation
    measured: str     # often empty — means not tested (not an error)
    remarks: str      # "Comply" / "Non-Compliant" / etc.
```

**Key design decision:** `measured` being empty is explicitly treated as "not tested" (acceptable), NOT as an error. Real UAE submittals frequently omit measured values — Experiment B confirmed this.

The LLM system prompt maps alternative column names (e.g. "As Offered" → proposed, "Test Result" → measured) so the extraction works regardless of how the contractor named their columns.

### What Agent 4 actually does: retrieve contexts and audit

1. **Get supporting contexts:**
   - `assemble_spec_context()` once → cached, so no extra API cost if spec_verifier already called it
   - `store.get_text(DocType.TECHNICAL_DATASHEET)` — full datasheet text
   - `store.get_text(DocType.TEST_REPORT)` — full test report text

2. **Single batched audit LLM call (per batch of 25 rows):**
   ALL rows are audited in batches of `_BATCH_SIZE = 25`. Each batch is one GPT-4o-mini call. The prompt sends:
   - Authority spec context (truncated to 2,000 chars)
   - Datasheet context (truncated to 1,500 chars)
   - Test report context (truncated to 1,500 chars)
   - Up to 25 rows as a numbered text block

   The LLM returns one audit result per row. The response must have the same number of rows as the request. If fewer come back, missing rows default to `proposed_verified=False, severity=warning`.

### Deviation rule (critical — enforced in LLM prompt)
A proposed value that **exceeds** a minimum requirement is NOT a deviation. Example: spec says "min 12% water reduction", contractor proposes "15%" — this is PASS, not a deviation. The LLM is explicitly instructed about this because it is non-obvious and easy to get wrong.

---

## 14. Agent 5 — Consistency Checker (Rule-Based)

**File:** `src/agents/consistency_checker.py`
**Rules in:** `src/rules/name_matcher.py`

No AI. rapidfuzz fuzzy string matching.

### What it checks
Loads the knowledge store: `store = load_store(state["knowledge_store_id"])`.

Reads entity names from the store (extracted from cover page by Agent 1):
- `store.manufacturer_name`
- `store.supplier_name`

For each relevant document type, checks that the entity name appears in `section.text[:1000]` (first 1,000 chars of the full extracted text from the knowledge store).

- Manufacturer name must appear consistently in: TECHNICAL_DATASHEET, TEST_REPORT, MANUFACTURER_GUARANTEE, DED_REGISTRATION, MSDF, MAF, COVER_PAGE
- Supplier name must appear consistently in: COVER_PAGE, MSDF, MAF

### How fuzzy matching works
For each document section of the relevant types:
1. Take `section.text[:1000]` from the knowledge store (full text, truncated to first 1,000 chars for efficiency)
2. Find the best candidate line that shares keywords with the entity name
3. Compare candidate line against entity name using `fuzz.token_sort_ratio`
4. Score ≥ 85 → match (consistent)
5. Score < 85 → WARNING finding

`token_sort_ratio` sorts tokens alphabetically before comparing. This handles "LLC Emirates Contracting" vs "Emirates Contracting LLC" — word order doesn't matter.

**Why threshold = 85?** This handles OCR noise ("Em!rates" → "Emirates") and abbreviation variants ("L.L.C." vs "LLC") while still catching genuine inconsistencies.

### What this agent cannot detect
- Different names that are genuinely similar (two companies with similar names)
- OCR noise so severe that the name is unrecognizable
- Cases where the manufacturer name appears only after the first 1,000 chars of a section

---

## 15. Agent 6 — AVL Checker (TAQA Only)

**File:** `src/agents/avl_checker.py`

### When it runs
Only when `authority == "TAQA"`. For ADM, the `skip_avl` node runs instead and writes an empty list to `avl_findings`.

### How AVL search works
1. Load knowledge store and search sections for any doc typed as `PREVIOUS_APPROVAL` or `OTHERS` that contains "approved vendor" or "vendor list" in its text
2. If found → fuzzy-match manufacturer name against each line of AVL text
3. Match found → PASS
4. No match → CRITICAL

The AVL document is expected to be uploaded as part of the submittal package. If it's missing entirely → CRITICAL finding, but review continues.

**Design decision:** The review ALWAYS continues regardless of AVL status. The AVL check is informational — it tells you the status, but doesn't stop the rest of the review. The report will say RESUBMIT if AVL is CRITICAL, but all other stages still run.

---

## 16. Agent 7 — Report Compiler

**File:** `src/agents/report_compiler.py`

### What it does
Gathers all findings from all state keys, counts criticals and warnings, determines the recommendation, generates professional summary comments with GPT-4o-mini, and builds the final `ReviewReport`.

### Recommendation logic
```python
if critical_count > 0:     → "RESUBMIT"
elif warning_count > 2:    → "CONDITIONAL"
else:                      → "APPROVE"
```

Note: `critical_count` and `warning_count` are computed here AND separately by `ReviewReport`'s `@model_validator`. The agent computes them manually to determine the recommendation before building the report object. The model_validator recomputes them from the finding lists for data integrity.

### Summary generation
Sends a digest to GPT-4o-mini:
```
Authority: ADM
Material: [description]
Clause: [spec_clause]
Recommendation: RESUBMIT
Critical issues (3): expired DED; wrong table value; missing method statement
Warnings: 2
Missing documents: test_report, method_statement
```
Returns 2-4 professional sentences summarizing the review. Temperature=0 for deterministic output.

---

## 17. Agent 8 — Query Agent (Post-Review Chat)

**File:** `src/agents/query_agent.py`

This agent does NOT run in the LangGraph pipeline. It is called separately after the review completes, via `handle_query(state, question)`.

### How question routing works
First call: classify the question into one of three routes using GPT-4o-mini:
- `spec_rag` — "What does the spec require for minimum tensile strength?" → search ChromaDB spec database
- `submittal_rag` — "What did the manufacturer's datasheet say about pH?" → search submitted document embeddings
- `report_json` — "What was the overall recommendation?" → use the report JSON directly

### `spec_rag` path
Calls `assemble_spec_context(question=question, clause_ref=spec_clause, authority=authority)` → same RAG pipeline as during review (with caching). Returns a grounded answer with source reference.

### `submittal_rag` path
Uses `src/rag/submittal_rag/retriever.py` — queries the per-session submittal collection (not the spec database). Semantic-only retrieval (no BM25, no reranking) — post-review Q&A queries are conversational, not spec-term lookups, so pure semantic is appropriate. Returns top-5 chunks with source filenames.

**Important:** The submittal collection must be populated before Q&A works. The embedder (`src/rag/submittal_rag/embedder.py`) must be called after the review completes to embed all submitted documents. This is a **known pending item** — as of Phase 5, the embedder is not called automatically after review. The `submittal_rag` route will return empty results until this is wired up.

### `report_json` path
Flattens the report dict into a text summary and sends it as context. The LLM answers from the summary — no vector search needed for questions about the review itself.

### Conversation history
`handle_query()` wraps `query_agent_node()` and appends each Q&A turn to `conversation_history` in state. This means multi-turn conversation is supported — each new question can reference prior answers.

---

## 18. Rule-Based Components

These three modules in `src/rules/` contain NO AI. They are deterministic Python functions.

### `date_checker.py` — When to use AI would be wrong
Date checks are pass/fail with exact rules (expired = CRITICAL, not expired = PASS). Using AI here would introduce uncertainty where there is none. The rules are:
- DED: expiry date must be future
- Test reports: date must be within 3 years
- Guarantee: stated period must meet spec minimum (currently only warns if period not found)

### `completeness.py` — Pure set comparison
Takes the set of `DocType` values present in `store.get_present_types()`. Compares against the `required_doc_types` list from the authority profile. Missing types → CRITICAL finding per missing type.

Special case: `MAF` and `PREVIOUS_APPROVAL` are interchangeable for Index 8. If either is present, both are added to the present set before comparison.

Mismatch detection: any section with `mismatch_flagged=True` (set by the classifier when the declared section doesn't match the actual doc_type) gets a WARNING finding.

### `name_matcher.py` — Why rapidfuzz over exact matching
Exact matching would fail on: "Emirates LLC" vs "Emirates L.L.C.", OCR noise ("Em1rates"), Arabic/English name variations. rapidfuzz handles all of these. Threshold 85 was calibrated in Experiment A.

---

## 19. Authority Profiles

**Files:** `src/config/base_profile.py`, `src/config/adm_profile.py`, `src/config/taqa_profile.py`

The authority profile pattern allows adding new authorities (DMT, ADDC, etc.) without changing any existing code.

### `AuthorityProfile` (abstract base)
Defines the interface:
- `authority` → string code ("ADM", "TAQA")
- `index_items` → dict of item numbers to descriptions
- `required_doc_types` → list of DocTypes that must be present
- `optional_doc_types` → types not flagged as missing (defaults: PREVIOUS_APPROVAL, OTHERS)
- `requires_avl_check` → whether AVL check runs (default False)
- `requires_non_toxicity` → TAQA-specific (default False)
- `get_max_doc_age_days(doc_type)` → max age in days for dated docs (abstract)
- `chroma_collection_name` → derived: `f"{authority.lower()}_specifications"`

### `ADMProfile`
- 12 required doc types (all standard index items except PREVIOUS_APPROVAL)
- Test reports: max 3 years (3 × 365 days)
- DED: governed by certificate expiry date (no fixed age limit)
- Guarantee: no fixed period requirement in current code

### TAQA profile
Not yet implemented. When added, it will subclass `AuthorityProfile` and override `requires_avl_check = True` and add AVL/non-toxicity to `required_doc_types`.

### `get_authority_profile(authority: str)` (`src/config/__init__.py`)
Returns the singleton profile object. Called from orchestrator and agents.

---

## 20. Streamlit UI Layer

**Files:** `app/main.py`, `app/pages/`

The UI layer is a 5-page Streamlit application. All pages share `st.session_state` for review data.

### Session state contract
Defined once in `app/main.py`:
```python
{
    "page":                 "upload",     # current page key
    "authority":            "ADM",        # "ADM" or "TAQA"
    "metadata":             None,         # SubmittalMetadata.model_dump()
    "review_complete":      False,        # True once LangGraph run finishes
    "knowledge_store_id":   None,         # path to SubmittalKnowledgeStore JSON
    "report":               None,         # ReviewReport.model_dump()
    "conversation_history": [],           # list of ConversationTurn dicts
}
```

### Navigation
Custom sidebar navigation via `st.session_state.page`. Three pages (`review`, `report`, `chat`) are disabled (`st.button(disabled=True)`) until `review_complete` is True.

Native Streamlit multi-page routing was rejected because it provides no way to disable individual page links. See `engineering_log.md` Issue 5.5.

### `app/pages/upload.py`
- Authority selectbox → `st.session_state.authority`
- Multi-file PDF uploader
- Per-file label selectboxes (matches `profile.index_items`)
- On submit: creates `SubmittalMetadata`, calls `save_upload()` to write PDFs to `data/submittals/{id}/`, calls `stage_files()`, sets `st.session_state.metadata`, navigates to `"review"`

### `app/pages/review.py`
- `@st.cache_resource` on `_get_graph()` — compiles LangGraph once per process
- Calls `graph.stream(initial_state)` inside `st.status()` context
- Shows each completed node as a status line in real time
- Accumulates state across events: `accumulated.update(node_state)`
- After graph ends: saves `report` and `knowledge_store_id` to session state, sets `review_complete=True`, calls `st.rerun()`

### `app/pages/report.py`
- Renders all 9 finding stages in expandable sections
- Critical stages auto-expand; PASS stages collapse
- Comparison table audit has per-row 4-column grid layout
- Copy-pasteable plain-text report in a `st.text_area`
- No PDF generation (plain text is sufficient for the prototype)

### `app/pages/chat.py`
- Suggested questions shown when conversation history is empty
- Routes to `handle_query(state, question)` for each user message
- Displays source routing per answer (`spec_rag` → "Authority Specification", etc.)
- `st.session_state.conversation_history` persists turns within the session

### `app/pages/spec_manager.py`
- Shows current ChromaDB collection status (chunk count per authority)
- Upload a spec PDF + provide authority + spec book name → call `index_spec_pdf()`
- Optional "reset collection" checkbox before indexing
- Saves spec PDF to `data/specs/{authority}/` via `save_upload()` before indexing

### `sys.path` fix
`app/main.py` has this as its first lines:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
```
Streamlit adds `app/` (the script's directory) to `sys.path`, not the project root. Without this fix, `from src.config.paths import ensure_dirs` fails with `ModuleNotFoundError: No module named 'src'`. See `engineering_log.md` Issue 5.4.

---

## 21. End-to-End Data Flow

Trace exactly what happens from file upload to report:

```
1. User uploads PDFs in Streamlit UI (upload.py)
   → authority = "ADM"
   → uploaded files: [ded.pdf, datasheet.pdf, cover.pdf, ...]
   → per-file declared labels: {"ded.pdf": "DED Registration", ...}
   → SubmittalMetadata created: {submittal_id: "a1b2c3d4-...", authority: "ADM", ...}

2. UI saves files to disk and stages bytes:
   → save_upload("data/submittals/{id}/", "ded.pdf", bytes) → called per file
   → stage_files(submittal_id, {filename→bytes}, {filename→label})
   → bytes deposited in _staging dict (NOT in state)

3. Initial LangGraph state built (small — no bytes):
   state = {
     "authority": "ADM",
     "submittal_id": "a1b2c3d4-...",
     "review_date": "2026-06-25",
   }

4. graph.stream(state) begins; UI renders each completed node as a status line.

5. Node: doc_processor
   → Pops bytes from _staging[submittal_id]
   → Classifies all documents (GPT-4o-mini per section)
   → Extracts full text for each section
   → Runs cover page extraction → fills store.{material_description, spec_clause, ...}
   → Pre-parses comparison table rows → fills store.table_rows
   → Saves SubmittalKnowledgeStore to data/knowledge_stores/{id}.json
   → Returns: {**state, "knowledge_store_id": "/path/to/{id}.json"}
   (Bytes consumed here — no longer needed or accessible)

6. Node: completeness
   → store = load_store(state["knowledge_store_id"])
   → check_completeness(store.get_present_types(), ADM_profile)
   → Also flags store.get_mismatches() as warnings
   → Writes: completeness_findings, missing_documents

7. Node: boq_drawing  [STUB — returns empty list]

8. Node: spec_verifier
   → store = load_store(state["knowledge_store_id"])
   → clause_ref = store.spec_clause
   → assemble_spec_context(clause_ref, "ADM") → full RAG pipeline → spec text
   → submitted_text = store.get_text(DocType.SPECIFICATION_COPY)
   → GPT-4o-mini: compare submitted spec vs authority spec
   → Writes: spec_verification_findings

9. Node: validity_checker
   → store = load_store(state["knowledge_store_id"])
   → For each DED/test_report/guarantee section: extract text → regex dates → compare to today
   → Writes: validity_findings

10. Conditional: _should_run_avl
    → authority == "ADM" → go to skip_avl

11. Node: skip_avl  → writes avl_findings = []

12. Node: statement  [STUB — returns empty list]

13. Node: table_auditor
    → store = load_store(state["knowledge_store_id"])
    → table_rows = [TableRow.model_validate(r) for r in store.table_rows]  ← pre-parsed by Agent 1
    → assemble_spec_context() [CACHED — no extra API calls]
    → store.get_text(DocType.TECHNICAL_DATASHEET), store.get_text(DocType.TEST_REPORT)
    → Single GPT-4o-mini call per batch of 25 rows
    → Writes: table_audit_findings

14. Node: consistency_checker
    → store = load_store(state["knowledge_store_id"])
    → manufacturer_name = store.manufacturer_name  ← from cover page extraction in Agent 1
    → For each relevant section: fuzzy match against section.text[:1000]
    → Writes: consistency_findings

15. Node: others  [STUB — returns empty list]

16. Node: report_compiler
    → Gather all findings from state
    → Count criticals and warnings
    → Determine recommendation (RESUBMIT/CONDITIONAL/APPROVE)
    → GPT-4o-mini: generate summary comments
    → Build ReviewReport object
    → Writes: report, review_complete=True

17. Graph ends (END node)
    → Final state returned to UI

18. UI saves report:
    → st.session_state.report = state["report"]
    → st.session_state.knowledge_store_id = state["knowledge_store_id"]
    → st.session_state.review_complete = True
    → st.rerun() → navigates to "review complete" view with recommendation badge

19. User navigates to report.py → sees all findings, copy-pastes plain text report

20. User asks question in chat.py
    → handle_query(minimal_state, question)
    → GPT-4o-mini routes question to spec_rag/submittal_rag/report_json
    → [submittal_rag: pending — submittal collection not yet populated after review]
    → Retrieves context from appropriate source
    → GPT-4o-mini answers grounded in context
    → Answer stored in conversation_history
```

---

## 22. ChromaDB Storage Structure

ChromaDB stores all data locally. The path is `CHROMA_PATH` from `src/config/paths.py`, which resolves to `{PROJECT_ROOT}/data/chromadb/`. This is an absolute path that does not depend on the working directory.

### Collection 1 — Spec database (permanent)
```
Collection name: adm_specifications
Documents: spec text chunks (500 chars each)
IDs: "ADM_irrigation_00042_c0003"
Metadata per chunk: {
  authority: "ADM",
  network: "irrigation",       ← primary filter key
  division: "33",
  section: "33 10",
  clause: "33 10 13",
  source_file: "ADM_Irrigation_Spec.pdf",
  chunk_index: 3,
  parent_id: "ADM_irrigation_00042"  ← used by parent_fetcher
}
```

### Collection 2 — Per-session submittal (temporary, pending implementation)
```
Collection name: "submittal_{submittal_id}"
Documents: submitted PDF text chunks (500 chars each)
IDs: "{filename}_c{index:04d}"
Metadata per chunk: {
  filename: "datasheet.pdf",
  doc_type: "technical_datasheet",
  chunk_index: 7
}
Status: schema defined in submittal_rag/store.py, but embedder.py is not yet called
        after review completes. The submittal_rag query route returns empty results
        until this is wired up. See engineering_log.md Future Known Issue #1.
```

### The parent-child relationship
When the query pipeline retrieves top-5 chunks, those 500-char fragments are too small for full clause context. The parent fetcher looks up the `parent_id` from each chunk's metadata, then fetches ALL chunks with that `parent_id` (the full clause), sorts by `chunk_index`, and concatenates them. This gives the LLM the complete spec clause instead of just 500 chars.

This is the "hierarchical retrieval" pattern: retrieve small chunks for precision, then expand to parent for full context.

### Local disk structure
```
data/
├── chromadb/              ← ChromaDB SQLite files (PersistentClient)
├── knowledge_stores/      ← {submittal_id}.json per review (SubmittalKnowledgeStore)
├── specs/
│   ├── adm/               ← admin-uploaded ADM spec PDFs (for traceability)
│   └── taqa/              ← admin-uploaded TAQA spec PDFs
└── submittals/
    └── {submittal_id}/    ← user-uploaded submittal PDFs (saved by upload.py)
```

---

## 23. Common Failure Modes and How to Diagnose Them

### Problem: spec_verifier returns "clause not found"
**Cause:** `assemble_spec_context()` returned `EMPTY_CONTEXT_SENTINEL`

**Diagnosis steps:**
1. Check if ChromaDB collection exists: `chromadb.PersistentClient(path=CHROMA_PATH).list_collections()`
2. Check if any docs exist for this network: `collection.get(where={"network": "irrigation"})`
3. Check `_CLAUSE_TO_NETWORK` in `query_constructor.py` — is the clause prefix mapped?
4. If clause is "33 10 13", does the map have "33 10"? (prefix match, not exact match)
5. Run the indexing pipeline for the missing spec book via Spec Manager

### Problem: doc_processor classifies documents wrong
**Cause:** OCR quality is poor, or the document genuinely looks like a different type

**Diagnosis steps:**
1. Load the knowledge store and check `section.confidence` — if there are low-confidence sections, Agent 1 actually filtered them out (only "high" and "medium" are stored). The document may have been skipped.
2. Check `section.text` in the knowledge store — is the extracted text readable?
3. If OCR returns garbage: Tesseract language pack might be missing, or image resolution is too low
4. If text is fine but classification is wrong: the system prompt may need a new example for this document pattern

### Problem: table_auditor returns no rows
**Cause 1:** `store.table_rows` is empty — Agent 1 found no table content
- Check if any section has `doc_type == COMPARISON_TABLE` in the knowledge store
- Comparison tables sometimes get classified as `specification_copy` if they have dense spec text

**Cause 2:** OCR returned fewer than 5 words per page in Agent 1's table extraction run
- The page might be an image with no text layer
- OCR at 2× zoom should handle this — check if Tesseract is installed and working

**Cause 3:** LLM returned zero rows during Agent 1's `extract_all_table_rows()` call
- Check `store.table_rows` — if it is `[]` and `store.has_type(DocType.COMPARISON_TABLE)` is True, the LLM saw the page but found no table structure

### Problem: consistency_checker gives false positives (flags names that are actually the same)
**Cause:** `section.text[:1000]` doesn't reach the manufacturer name if it appears deep in the document

**Diagnosis:**
- The consistency checker uses `section.text[:1000]` from the knowledge store
- If the manufacturer name is on page 2 or after the header section, it won't be found
- The `_extract_entity_from_preview()` function looks for the best keyword-matching line
- If no keywords match, it falls back to first 80 chars — which may not contain the name

**Fix path:** Increase the text slice size for consistency checks (currently 1,000 chars)

### Problem: BM25 returns wrong results
**Cause:** The `_build_bm25_for_network` cache is stale after new specs are indexed

**Diagnosis:** The BM25 index is built from ChromaDB at process startup and cached with `@lru_cache`. If new documents were added to ChromaDB (via Spec Manager) after process start, the BM25 index doesn't see them until process restart.

**Fix:** Restart the Streamlit server after indexing (or add a "Clear BM25 Cache" button in Spec Manager that calls `_build_bm25_for_network.cache_clear()`)

### Problem: LangGraph state missing fields
**Cause:** A node returned a dict without `{**state, ...}` — it wiped earlier fields

**Diagnosis:** Add logging to each node's return value. Check that every node returns `{**state, "new_field": value}`.

### Problem: knowledge_store_id missing from state in downstream agent
**Cause:** Agent 1 did not write it (usually because `_staging.pop()` returned empty — bytes were never staged)

**Diagnosis:**
1. Check that `stage_files(submittal_id, ...)` was called BEFORE `graph.invoke()` in the UI
2. Check that the `submittal_id` matches between `stage_files()` and the state dict passed to `graph.invoke()`

### Problem: Cohere reranker API error
**Cause:** `COHERE_API_KEY` missing or invalid

**Diagnosis:**
1. Check `os.environ["COHERE_API_KEY"]` exists
2. The reranker skips the API call if `len(candidates) <= TOP_N` (5) — check if this is the case
3. If error, `rerank()` will raise an exception — wrap in try/except to degrade gracefully

### Problem: report recommendation seems wrong
**Trace the count logic:**
1. `ReviewReport._compute_counts()` recomputes critical/warning from the finding lists
2. A PASS finding should not increment either count — verify `severity == Severity.PASS`
3. If recommendation is RESUBMIT with 0 criticals: check table_audit_findings — they have their own critical counter
4. `_determine_recommendation()` in report_compiler.py uses the pre-model counts, not model_validator counts — both should be identical but verify if inconsistency seen
