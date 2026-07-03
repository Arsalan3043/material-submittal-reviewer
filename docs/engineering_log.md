# Engineering Log — Material Submittal Reviewer

> This document records every significant issue, error, architectural debate, and decision
> encountered during Phases 3–5 of the build. It is written so that anyone can read it cold
> and understand not just what happened, but why, what we were thinking, what we tried,
> and why we ultimately chose the path we did.
>
> **Phase 0–2 issues** (infrastructure setup, RAG experiments, LLM component testing)
> are documented in `docs/experiment_findings.md`.
> This file picks up from Phase 3 (Production Build) onwards.
>
> This file is a living document. Every future change, regression, or rethink gets added here.

---

## Table of Contents

1. [Phase 3 — Production Build Issues](#phase-3--production-build-issues)
   - [Issue 3.1 — PDF Bytes Inside LangGraph State](#issue-31--pdf-bytes-inside-langgraph-state)
   - [Issue 3.2 — All Agents Reading Directly From Classified Documents](#issue-32--all-agents-reading-directly-from-classified-documents)
   - [Issue 3.3 — LangSmith Tracing Broke on Agent Functions](#issue-33--langsmith-tracing-broke-on-agent-functions)
   - [Issue 3.4 — ChromaDB Path Hardcoded in Three Separate Files](#issue-34--chromadb-path-hardcoded-in-three-separate-files)
2. [Phase 4 — Agent Scenario Testing Issues](#phase-4--agent-scenario-testing-issues)
   - [Issue 4.1 — Scenario 1 and 2 Errors: State Fields Missing Downstream](#issue-41--scenario-1-and-2-errors-state-fields-missing-downstream)
3. [Phase 5 — UI Build Issues](#phase-5--ui-build-issues)
   - [Issue 5.1 — ChromaDB Cloud Abandoned: 300-Record Quota vs 500-Char Chunks](#issue-51--chromadb-cloud-abandoned-300-record-quota-vs-500-char-chunks)
   - [Issue 5.2 — Everything Local: How to Handle Storage in Streamlit](#issue-52--everything-local-how-to-handle-storage-in-streamlit)
   - [Issue 5.3 — Architecture Debate: Submittal ID Design for Production Transition](#issue-53--architecture-debate-submittal-id-design-for-production-transition)
   - [Issue 5.4 — ModuleNotFoundError: No module named 'src'](#issue-54--modulenotfounderror-no-module-named-src)
   - [Issue 5.5 — Streamlit Navigation: Custom Sidebar vs Native Multi-Page](#issue-55--streamlit-navigation-custom-sidebar-vs-native-multi-page)
   - [Issue 5.6 — File I/O Scattered Across the Pipeline](#issue-56--file-io-scattered-across-the-pipeline)
4. [Phase 5 (Continued) — Specification Verification Architecture Overhaul](#phase-5-continued--specification-verification-architecture-overhaul)
   - [Issue 5.7 — Spec Verifier Had No Structured Requirement Model](#issue-57--spec-verifier-had-no-structured-requirement-model)
   - [Issue 5.8 — Pydantic ValidationError: Boolean Fields Returning Null](#issue-58--pydantic-validationerror-boolean-fields-returning-null)
   - [Issue 5.9 — Stage 3 Reported Missing Evidence That Stage 9 Already Found](#issue-59--stage-3-reported-missing-evidence-that-stage-9-already-found)
   - [Issue 5.10 — Wrong Operators Extracted for Performance Requirements](#issue-510--wrong-operators-extracted-for-performance-requirements)
   - [Issue 5.11 — Installation Requirements Expected Evidence From Technical Datasheet](#issue-511--installation-requirements-expected-evidence-from-technical-datasheet)
   - [Issue 5.12 — Contradiction Detection Threshold Too Aggressive (5%)](#issue-512--contradiction-detection-threshold-too-aggressive-5)
   - [Issue 5.13 — `comparison_table_required` Extracted But Never Read](#issue-513--comparison_table_required-extracted-but-never-read)
   - [Issue 5.14 — Deterministic Override Only Ran If LLM Found Evidence First](#issue-514--deterministic-override-only-ran-if-llm-found-evidence-first)
   - [Issue 5.15 — Prompt Instructed GPT to Do Numeric Comparison (Python's Job)](#issue-515--prompt-instructed-gpt-to-do-numeric-comparison-pythons-job)
   - [Issue 5.16 — Standard and Material Requirements Left to LLM Judgment](#issue-516--standard-and-material-requirements-left-to-llm-judgment)
5. [Architectural Debates and Reasoning](#architectural-debates-and-reasoning)
   - [Debate A — ChromaDB Cloud vs Local PersistentClient](#debate-a--chromadb-cloud-vs-local-persistentclient)
   - [Debate B — Production Transition Readiness of Local-First Architecture](#debate-b--production-transition-readiness-of-local-first-architecture)
   - [Debate C — Evidence Architecture: Document Comparison vs Requirement-Centric](#debate-c--evidence-architecture-document-comparison-vs-requirement-centric)
   - [Debate D — Where Should Numeric Verification Live?](#debate-d--where-should-numeric-verification-live)
6. [Future Known Issues and Watch Points](#future-known-issues-and-watch-points)

---

## Phase 3 — Production Build Issues

Phase 3 was the translation of proven experiment code into production-grade agents under `src/`. Most of the Phase 3 issues came from two sources: (1) LangGraph's constraints on what can live in state, and (2) the discovery that all agents needed a shared persistent knowledge layer rather than re-reading from state on every call.

---

### Issue 3.1 — PDF Bytes Inside LangGraph State

**When it appeared:** During the initial design of `src/agents/state.py` and the first complete run of the orchestrator.

**What happened:**

The original `SubmittalReviewState` schema included `file_contents: dict[str, bytes]` — a mapping of filename to raw PDF bytes. This was the obvious design: the user uploads files, those bytes need to reach `doc_processor`, so put them in state.

The problems became apparent immediately in two places:

**Problem A — LangSmith trace size:**
LangGraph sends the full state as part of each LangSmith trace event. A typical submittal package is 5–15 PDF files, each 2–20 MB. A 10-file submittal at 5 MB per file = 50 MB in every single LangSmith trace event, across every node transition. LangSmith's UI froze when trying to display traces. The dashboard showed error messages about payload size limits. Tracing became effectively unusable.

**Problem B — Memory:**
When Streamlit runs multiple concurrent user sessions (even in development, if you refresh), each session's LangGraph state sits in memory until the graph completes. With bytes in state, memory usage scaled linearly with file sizes. On the development machine, the process hit 2+ GB for a single run.

**Thought process to resolve:**

The core question was: where do PDF bytes belong, if not in state?

Three options were considered:

*Option 1 — Write bytes to disk before invoking the graph, pass file paths in state.*
Paths are tiny strings. State stays small. Agents open files when they need them.
Problem: this couples all agents to the local filesystem. Any agent that opens a file creates an implicit dependency on a local path. In production (cloud deployment), those paths won't exist.

*Option 2 — A module-level staging dictionary, keyed by submittal_id.*
Before calling `graph.invoke()`, the caller deposits bytes in a `_staging` dict. The first node (`doc_processor`) reads and removes them immediately. No bytes ever enter LangGraph state or LangSmith traces.
Problem: the staging dict is process-local. If the graph is run in a separate process or worker, the staging dict would be empty. For the current architecture (single-process Streamlit), this is fine.

*Option 3 — Pass bytes as a constructor argument to a custom LangGraph node class.*
Overly complex. LangGraph 0.1.x does not support stateful node classes well.

**Decision: Option 2 — staging dict.**

`stage_files(submittal_id, file_contents, declared_labels)` is called from the UI layer before `graph.invoke()`. `doc_processor_node` calls `_staging.pop(submittal_id, ({}, {}))` as its first action. This is a clean hand-off: bytes exist only for the duration of the first node, then disappear. All downstream agents receive only the knowledge store path (a short string).

**What changed in code:**
- Removed `file_contents` and `declared_labels` from `SubmittalReviewState`
- Added `stage_files()` function to `doc_processor.py`
- All downstream agents now read from `SubmittalKnowledgeStore` (text only)
- LangSmith traces went from multi-MB payloads to kilobyte payloads

**Lesson:**
LangGraph state is not a general-purpose data store. It is a schema for what needs to travel between nodes. Anything large that is consumed once (PDF bytes) should not live in state — use a side channel. The staging pattern works for single-process deployments. For distributed deployments, the staging dict would be replaced with a temporary blob URL passed as a string in state.

---

### Issue 3.2 — All Agents Reading Directly From Classified Documents

**When it appeared:** After the initial implementation of all 8 agents, during integration testing of the full graph run.

**What happened:**

The first implementation had `doc_processor` writing `classified_documents: Dict[str, ClassifiedDocument]` to state, and every downstream agent reading from that dict. For example, `validity_checker` would do:

```python
docs = {k: ClassifiedDocument.model_validate(v) for k, v in state["classified_documents"].items()}
for filename, doc in docs.items():
    if doc.doc_type == DocType.DED_REGISTRATION:
        text = doc.text_preview  # only first 500 chars
```

Two problems emerged:

**Problem A — `text_preview` is too short for full analysis.**
`ClassifiedDocument.text_preview` stored only the first 500 characters of extracted text. This was originally designed as a lightweight summary for consistency checks. But the validity checker needed the full DED certificate text to find expiry dates. The spec verifier needed the full spec copy text (often 3-5 pages) for comparison. The table auditor needed every page of the comparison table. Agents were silently getting incomplete data and producing wrong or missing findings.

**Problem B — State schema was getting bloated.**
As agents discovered they needed more data, the temptation was to add more fields to `ClassifiedDocument` in state. More pages, longer text, table rows, pre-parsed dates. State was growing back toward the bytes problem we solved in Issue 3.1 — just text instead of bytes.

**Problem C — Table rows were being parsed multiple times.**
The table extractor (pdfplumber + LLM) is expensive — one call per page, often 3-5 pages. In the original design, `table_auditor` called the extractor at runtime from the PDF bytes. But the PDF bytes were already gone from state (Issue 3.1 fix). So the table extractor needed bytes, which weren't available, which meant we either had to put bytes back in state or re-read from disk.

**Thought process to resolve:**

The fundamental question was: where should extracted text content live?

The insight came from recognizing that `doc_processor` is the only agent that ever reads PDF bytes. Every other agent only needs text. If `doc_processor` extracts ALL text upfront and writes it to a persistent store, no other agent needs to re-read any PDF.

The solution: `SubmittalKnowledgeStore` — a Pydantic model written to disk as a JSON file by `doc_processor`, then loaded (with module-level caching) by every downstream agent. State carries only the file path string (`knowledge_store_id`), which is ~80 characters.

```
doc_processor → reads PDF bytes → extracts all text → writes knowledge_store.json → returns path in state
all other agents → load_store(state["knowledge_store_id"]) → get text they need
```

This also solved the table row parsing problem: `doc_processor` runs `extract_all_table_rows()` upfront during its own execution and stores the structured rows in the knowledge store. `table_auditor` reads pre-parsed rows from the store — it never needs PDF bytes.

**The refactor sequence** (visible in git history):
Each agent was updated one at a time: spec_verifier → validity_checker → table_auditor → avl_checker → consistency_checker → report_compiler. Each PR removed `classified_documents` reads and replaced them with `load_store()` calls.

**What changed in code:**
- Created `src/models/knowledge_store.py` with `SubmittalKnowledgeStore` and `DocumentSection`
- `doc_processor` now builds the store during its run and saves it to `data/knowledge_stores/{submittal_id}.json`
- All agents call `load_store(state["knowledge_store_id"])` instead of reading from state
- Module-level `_cache` dict ensures the JSON file is read at most once per process per submittal
- `text` field on `DocumentSection` is the full extracted text (not preview) — extracted once, stored persistently
- `table_rows` field stores pre-parsed comparison table rows

**Lesson:**
"What does each agent actually need?" is a better question than "What should state carry?" When the answer to the first question is "text content of submitted documents," that belongs in a persistent store, not in transient graph state. The knowledge store pattern separates extraction (done once, by Agent 1) from analysis (done by all subsequent agents using extracted content).

---

### Issue 3.3 — LangSmith Tracing Broke on Agent Functions

**When it appeared:** After adding `@traceable` decorators to all agent functions and running the first end-to-end graph run with LangSmith tracing enabled.

**What happened:**

The `@traceable` decorator from `langsmith` wraps a function and sends its inputs, outputs, and timing to the LangSmith API. When applied to LangGraph node functions (which take `state: SubmittalReviewState` and return a dict), the decorator caused two problems:

**Problem A — Input serialization error:**
`SubmittalReviewState` is a `TypedDict`. When LangSmith tried to serialize the full state dict as the "inputs" to the traceable function, it hit fields whose values were lists of dicts containing nested Pydantic model data. In some cases, the serialization produced `None` for complex nested objects, causing LangSmith to show empty inputs in the trace.

**Problem B — Double-tracing with LangGraph:**
LangGraph already instruments every node transition for its own tracing. When `@traceable` was applied to the node function itself, there were two competing trace contexts — one from LangGraph and one from LangSmith's `@traceable`. This produced duplicate span entries in LangSmith and sometimes caused the trace to show the same LLM call twice.

**Thought process to resolve:**

The issue had two components that required different fixes:

*For Problem A (serialization):*
The `@traceable` decorator on node functions was removed. Instead, `@traceable` was kept only on the internal LLM-calling functions (e.g., `_extract_cover_page`, `_classify_document`, `_audit_table_rows`). These functions have clean, simple inputs (strings and lists of strings) that serialize correctly. The node function itself does not need to be traceable because LangGraph already traces it.

*For Problem B (double-tracing):*
LangGraph sends traces to LangSmith via the `LANGCHAIN_TRACING_V2=true` environment variable. Each LangGraph node appears as a step in the LangSmith run. Individual LLM calls inside those steps appear as sub-steps. The `@traceable` decorator on non-LLM functions was adding a third layer that confused the hierarchy.

The final pattern:
```python
# Node function — NOT decorated with @traceable (LangGraph handles this)
def doc_processor_node(state: SubmittalReviewState) -> SubmittalReviewState:
    ...
    cover_data = _extract_cover_page(text)  # this IS traceable
    ...

# LLM-calling function — decorated with @traceable
@traceable(name="extract_cover_page")
def _extract_cover_page(text: str) -> dict:
    response = _openai().chat.completions.create(...)
    ...
```

**What changed in code:**
- Removed `@traceable` from all node functions in `doc_processor.py`, `spec_verifier.py`, etc.
- Kept `@traceable` only on functions that make direct LLM calls
- Named each `@traceable` with a descriptive string: `@traceable(name="extract_cover_page")`, `@traceable(name="query_agent")`
- Verified in LangSmith that each review now shows: one top-level run (the graph) with child steps (each node) with grandchild steps (each LLM call)

**Lesson:**
LangGraph + LangSmith integration is opinionated about where tracing happens. LangGraph handles node-level tracing automatically. `@traceable` should only be used for sub-functions that make LLM or embedding calls and need their own visibility in the trace. Applying it to node functions creates trace hierarchy conflicts that are hard to debug in the LangSmith UI.

---

### Issue 3.4 — ChromaDB Path Hardcoded in Three Separate Files

**When it appeared:** During Phase 5 UI build, when analyzing how the Streamlit app would interact with the backend.

**What happened:**

The string `"data/chromadb"` was hardcoded independently in three different source files:
- `src/rag/indexing/indexer.py`: `_LOCAL_CHROMA_PATH = "data/chromadb"`
- `src/rag/query/hybrid_retriever.py`: `_LOCAL_CHROMA_PATH = "data/chromadb"`
- `src/rag/submittal_rag/store.py`: `_LOCAL_CHROMA_PATH = "data/chromadb"`

And similarly, `src/models/knowledge_store.py` had:
```python
_STORE_DIR = Path("data/knowledge_stores")
```

These are relative paths. They resolve relative to the process's current working directory (CWD). From the command line at the project root, this works. But it creates a silent fragility: if the CWD is anything other than the project root, all three ChromaDB clients open (or create) a new empty database in the wrong directory, and all existing indexed data becomes invisible.

When Streamlit is invoked with `streamlit run app/main.py`, the CWD is still the project root (wherever the command is run from), so the relative paths work in practice. But this is a convention, not a guarantee.

A deeper problem: if we ever switch to running agents in a subprocess, background thread, or task queue — where the CWD may differ — the entire knowledge base silently disappears from the process's view.

**Thought process to resolve:**

The fix needed to be: compute paths relative to a known fixed point, not relative to CWD.

The natural fixed point is the project root, which can be computed from any file's `__file__` attribute:

```python
# src/config/paths.py
PROJECT_ROOT = Path(__file__).parent.parent.parent
# __file__ is src/config/paths.py
# .parent → src/config/
# .parent.parent → src/
# .parent.parent.parent → project root
DATA_DIR = PROJECT_ROOT / "data"
CHROMA_PATH = str(DATA_DIR / "chromadb")
```

This gives an absolute path that is correct regardless of CWD, regardless of where the process was started.

The secondary decision was: put ALL path constants in one file (`src/config/paths.py`) and import from there everywhere. This means there is exactly one place to change when moving from local disk to cloud storage.

The tertiary addition was `ensure_dirs()` — a function that creates all four data subdirectories if they don't exist. Called from `app/main.py` on startup so the app never fails on a fresh clone with no `data/` directory.

**What changed in code:**
- Created `src/config/paths.py` with `PROJECT_ROOT`, `DATA_DIR`, `CHROMA_PATH`, `STORE_DIR`, `SPECS_DIR`, `SUBMITTALS_DIR`, and `ensure_dirs()`
- Updated `indexer.py`, `hybrid_retriever.py`, `submittal_rag/store.py` to import `CHROMA_PATH` from paths.py
- Updated `knowledge_store.py` to import `STORE_DIR` from paths.py
- Called `ensure_dirs()` in `app/main.py` as the first action after imports

**Lesson:**
Relative paths are fine for scripts run from a known directory. They are a liability in a web application where the CWD is implicitly set by the web server process. Always use absolute paths derived from `__file__` in library code. Centralize all path constants in one file. The cost of doing this is one import; the benefit is that path-related bugs become impossible.

---

## Phase 4 — Agent Scenario Testing Issues

---

### Issue 4.1 — Scenario 1 and 2 Errors: State Fields Missing Downstream

**When it appeared:** During Phase 4 agent scenario testing. Referenced in git commit `cb1ed6f: fixes some errors after scenario test 1 and 2`.

**What happened:**

Scenario 1 (complete correct submittal → expect APPROVE) and Scenario 2 (missing 2 documents → expect RESUBMIT with 2 flagged missing) both failed during integration testing. The errors were not in the review logic itself but in how nodes were passing state forward.

Two specific patterns caused the failures:

**Pattern A — Node returning partial state:**

Some nodes, when first written, returned only the fields they set rather than the full spread state:

```python
# Incorrect
def _completeness_node(state):
    findings, missing = check_completeness(...)
    return {
        "completeness_findings": findings,
        "missing_documents": missing,
    }

# Correct
def _completeness_node(state):
    findings, missing = check_completeness(...)
    return {
        **state,
        "completeness_findings": [f.model_dump() for f in findings],
        "missing_documents": missing,
    }
```

Without `**state`, every field set by all previous nodes was wiped. The `spec_verifier` then found `knowledge_store_id` missing from state and crashed.

**Pattern B — Pydantic objects stored directly instead of model_dump():**

Some early agent implementations stored Pydantic objects in state:
```python
return {**state, "completeness_findings": findings}  # findings is list[Finding]
```

LangGraph's TypedDict state cannot hold Pydantic objects cleanly — it expects plain JSON-serializable types. When a downstream node tried to validate the finding:
```python
Finding.model_validate(raw)  # raw was already a Finding, not a dict
```
This sometimes worked (Pydantic can validate from a model instance) and sometimes failed with serialization errors depending on the LangGraph version's internal serialization behavior.

**Thought process to resolve:**

The correct pattern for all nodes was established as a standard:

1. Always spread previous state: `return {**state, "new_field": value}`
2. Always call `.model_dump()` before storing any Pydantic object: `[f.model_dump() for f in findings]`
3. Always call `Model.model_validate(raw)` when reading findings back out of state: `Finding.model_validate(d)`

This mirrors the advice in the LangGraph documentation for TypedDict state: state is a plain dict, not a typed container. All serialization/deserialization must be explicit.

After applying this fix to every node that was returning partial state or storing Pydantic objects directly, both Scenario 1 and Scenario 2 passed.

**What changed in code:**
- All node functions audited for `**state` spread
- All finding lists converted to `[f.model_dump() for f in ...]` before storing in state
- All reading code in `report_compiler` uses `Finding.model_validate(d)` and `TableRowFinding.model_validate(d)`
- This pattern is now enforced as a convention: "Pydantic in → model_dump → state → model_validate → Pydantic out"

**Lesson:**
LangGraph TypedDict state is a flat Python dict. It does not understand Pydantic models. Always treat the boundary between Python objects and LangGraph state as a serialization boundary. Serialize going in, deserialize coming out. Missing `**state` in a node return is one of the easiest bugs to introduce and one of the hardest to debug because the error appears in the next node, not the node that dropped the data.

---

## Phase 5 — UI Build Issues

---

### Issue 5.1 — ChromaDB Cloud Abandoned: 300-Record Quota vs 500-Char Chunks

**When it appeared:** During the Phase 5 architecture planning, before writing any UI code.

**What happened:**

The original plan (documented in `CLAUDE.md`) specified ChromaDB Cloud as the vector database for production. The plan was to use `chromadb.HttpClient` or the ChromaDB Cloud Python client in `src/` code, with local `PersistentClient` only for experiments.

This plan hit two problems that were already documented in `experiment_findings.md` (Problems 2, 3, and 8) but had not been fully resolved for the production code path:

**Problem A — 300-record quota on ChromaDB Cloud free tier:**
The free tier allows only 300 documents per collection. A single ADM spec PDF produces 7,000–20,000 chunks. There is no version of the production RAG pipeline that works within a 300-record limit.

**Problem B — 0.x to 1.x breaking schema change:**
ChromaDB's schema for collection configuration changed incompatibly between 0.5.x and 1.x. Collections created with the old schema had `config_json_str = '{}'` in the SQLite database, which caused `KeyError: '_type'` when the 1.x client tried to read them. This was documented and fixed for the local experiments (via SQLite patching), but would be an ongoing migration risk for Cloud collections.

**Thought process to resolve:**

The question was: should we keep trying to make ChromaDB Cloud work for Phase 5, or commit to local-first?

Arguments for pushing through with ChromaDB Cloud:
- Production deployment needs a cloud database anyway
- Better to solve this now than during Phase 6

Arguments for local-first in Phase 5:
- The 300-record quota is a fundamental product constraint that no engineering fix can resolve — it requires a paid tier or a different service
- The Phase 5 goal is a working end-to-end prototype, not production deployment
- Local `PersistentClient` already works (all experiments used it) and has no record limits
- The transition from local to cloud (or any other vector DB) is a one-line change in `paths.py` and `_chroma()` factory functions — it does not require rewriting any agent

**Decision: commit to local `PersistentClient` for Phase 5 and beyond until explicitly moving to production deployment.**

The production transition is designed to be non-breaking:
- `_chroma()` factory functions in 3 files → change one line each (or centralise in `paths.py`)
- `CHROMA_PATH` in `paths.py` → change to an `HttpClient` or cloud client call
- No agent, no model, no rule-based component changes

**What changed in code:**
- `PersistentClient` retained in all 3 ChromaDB-using modules
- All 3 modules updated to import `CHROMA_PATH` from `paths.py` (Issue 3.4 fix applied at same time)
- Confirmed: `data/chromadb/` already had 2 collections: `adm_clause` (11,480 chunks) and `adm_specifications` (20,629 chunks) — both working

---

### Issue 5.2 — Everything Local: How to Handle Storage in Streamlit

**When it appeared:** During Phase 5 planning, before building any UI pages.

**What happened:**

As the Phase 5 build started, we stepped back to map out exactly what storage each part of the UI needed to touch:

1. **Spec PDFs** — admin uploads them via `spec_manager.py`, they need to be saved somewhere, then passed to `index_spec_pdf()`
2. **Submittal PDFs** — users upload them via `upload.py`, they need to be saved somewhere, then passed to `stage_files()`
3. **ChromaDB** — already local at `data/chromadb/`, needs no UI intervention
4. **Knowledge stores** — already local at `data/knowledge_stores/`, written by `doc_processor`, read by agents
5. **Review report** — held in `st.session_state` during the session, no persistent storage needed for the prototype

The issue: `st.file_uploader` in Streamlit returns an `UploadedFile` object (in-memory bytes wrapped in a special class). It does NOT return a file path. But `index_spec_pdf()` and the PDF parsing functions all expect a file path or bytes.

**Three approaches considered:**

*Approach 1 — Pass bytes directly, never save to disk.*
`f.getvalue()` gets the bytes from a Streamlit uploader. Pass those directly to parsing functions without saving.
Problem: `index_spec_pdf()` takes `pdf_path: Path | str`. It opens the file with `fitz.open(stream=content, filetype="pdf")` internally. Refactoring it to accept bytes would break the existing interface and scatter the I/O concern into the core business logic.

*Approach 2 — Save to disk first, then pass the path.*
Save the uploaded bytes to `data/submittals/{submittal_id}/` or `data/specs/authority/` using a utility function. Then pass the saved path to the pipeline.
Advantage: all pipeline code continues to work exactly as is. The UI layer is the only thing that needs to know about saving files.
Disadvantage: files accumulate on disk. For a prototype with few users, this is fine.

*Approach 3 — Use a temporary file.*
`tempfile.NamedTemporaryFile()` creates a temp file, write bytes to it, pass the path. The temp file is deleted when the context manager exits.
Problem: the pipeline is async from the perspective of the UI (it runs in a Streamlit thread). If the temp file is deleted before the pipeline finishes, the file is gone mid-run.

**Decision: Approach 2 — save to `data/submittals/` and `data/specs/` subdirectories.**

The saving logic was extracted into a single function `save_upload(dest_dir, filename, data) -> Path` in `src/parsers/file_io.py`. This function is the only place in the codebase that writes user-uploaded data to disk. For the production transition (S3, Azure Blob), this is the one function to change.

**The full local storage map after Phase 5:**
```
data/
├── chromadb/              ← ChromaDB persistent store (spec + submittal embeddings)
├── knowledge_stores/      ← {submittal_id}.json per review
├── specs/
│   ├── adm/               ← admin-uploaded ADM spec PDFs
│   └── taqa/              ← admin-uploaded TAQA spec PDFs
└── submittals/
    └── {submittal_id}/    ← user-uploaded submittal PDFs
```

This `data/` directory is the entire local "database" for the system.

---

### Issue 5.3 — Architecture Debate: Submittal ID Design for Production Transition

**When it appeared:** During the `paths.py` / `SubmittalMetadata` design discussion in Phase 5.

**What happened:**

When discussing how to prepare for production multi-tenancy, two approaches were proposed for submittal identification:

**Approach A (initially proposed):** Embed scoping information in the ID itself:
```
submittal_id = f"{user_id}_{uuid.uuid4()}"
```
Rationale: every artifact (ChromaDB collection name, knowledge store filename, submittals directory) is automatically scoped to the user with no extra lookup needed.

**Approach B (counter-proposed, via external review):** Keep the ID clean, put scoping in metadata:
```python
class SubmittalMetadata(BaseModel):
    submittal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    authority: str
    project_name: str = ""
    # user_id: str | None = None     ← add when multi-tenancy needed
    # tenant_id: str | None = None   ← add when multi-tenancy needed
```

**The argument against Approach A:**

`{user_id}_{uuid}` bakes business logic into a storage key. When a tenant is renamed, when user accounts are merged, when you add a tenant layer above the user layer — every stored artifact (ChromaDB collection name, JSON filename, S3 key) would need to be renamed or migrated. In ChromaDB and local file systems, renaming collections and files is not atomic.

More fundamentally: the key is supposed to identify the artifact, not describe its access control. Access control belongs in the metadata layer, not the key.

**The argument against Approach B (the concern that was raised):**

If the ID is a plain UUID, then in production you cannot derive "who owns this submittal" from the ID alone. You always need to look up the metadata. This is a database lookup on every access.

**Why Approach B wins for this system:**

This is a material submittal review system, not a high-volume API. Every access to a review starts with the user logging in and selecting their submittal from a list — which is inherently a metadata query. The "we need the ID to tell us the owner" concern assumes a pattern (direct lookup by ID without metadata context) that does not exist in this workflow.

The metadata model is clean: `submittal_id` is opaque, `user_id` and `tenant_id` are fields on `SubmittalMetadata`. Adding them later is a non-breaking schema change. The `# user_id: str | None = None` comment in the current code documents exactly where to add them.

**Decision: Approach B — plain UUID for `submittal_id`, scoping in `SubmittalMetadata`.**

**What changed in code:**
- `SubmittalMetadata` added to `src/models/submittal.py` with `submittal_id`, `authority`, `project_name`, `created_at`
- `user_id` and `tenant_id` commented out with a note: "Production fields — add when multi-tenancy is needed"
- All session state references to the submittal ID go through `st.session_state.metadata["submittal_id"]`

---

### Issue 5.4 — ModuleNotFoundError: No module named 'src'

**When it appeared:** First run of `streamlit run app/main.py` after building all UI pages.

**What happened:**

```
ModuleNotFoundError: No module named 'src'

File "/Users/mdarsalanarshad/Documents/material-submittal-reviewer/app/main.py", line 3, in <module>
    from src.config.paths import ensure_dirs
```

`app/main.py` imports from `src.config.paths`. When run directly from the terminal (`python app/main.py`) or via pytest from the project root, this works because Python adds the CWD to `sys.path`. But when Streamlit runs a file, it adds the **directory containing the file** to `sys.path`, not the project root.

Streamlit's behavior:
```
streamlit run app/main.py
# Python sys.path includes: ['.../material-submittal-reviewer/app', ...]
# 'src' is at: '.../material-submittal-reviewer/src/'
# 'app/' does not contain 'src/', so import fails
```

**Why this is Streamlit-specific:**

Normal Python execution adds CWD to `sys.path[0]`. But Streamlit intercepts the script execution and adds the script's parent directory instead. This is documented Streamlit behavior — it mirrors how most web frameworks handle modules — but it breaks the convention that scripts at the project root can import from sibling packages.

**Approaches considered:**

*Approach 1 — Add a `pyproject.toml` or `setup.py` and install the package.*
`pip install -e .` would make `src` available as an installed package. Imports work anywhere.
Problem: requires restructuring — `src` would need to be a proper package with `__init__.py` at the right level, and the package name would need to be decided. Overkill for a prototype.

*Approach 2 — Add a `.pth` file to the Python environment.*
A `.pth` file in `site-packages` adds a directory to `sys.path` automatically.
Problem: environment-specific, not portable with the repository. Anyone cloning would need to do extra setup.

*Approach 3 — Add `sys.path.insert` at the top of `main.py`.*
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
```
This runs before any imports. `Path(__file__).parent.parent` is the project root (parent of `app/`, which is parent of `main.py`). After this line, `src` is findable.
Advantage: no setup required, works on any machine, co-locates the fix with the file that needs it.
Disadvantage: `sys.path` manipulation is generally discouraged in library code. Acceptable in application entry points.

**Decision: Approach 3 — `sys.path.insert` at the top of `app/main.py`.**

This is the standard pattern for Streamlit applications structured with a source tree outside the app directory. Flask and FastAPI have the same issue; the same fix is used.

**What changed in code:**
```python
# app/main.py — first 6 lines
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
```

**Lesson:**
Any Streamlit app that imports from a sibling package (not installed via pip) needs `sys.path.insert` at the top of the entry point. This is a one-time fix. Document it here so it is not a mystery when encountered again.

---

### Issue 5.5 — Streamlit Navigation: Custom Sidebar vs Native Multi-Page

**When it appeared:** During the design of `app/main.py`.

**What happened:**

Streamlit has two navigation mechanisms:

**Native multi-page routing (Streamlit 1.10+):**
Place Python files in a `pages/` directory relative to the entry script. Streamlit auto-generates a sidebar navigation from the filenames. Each page file runs independently on navigation. Page names are derived from filenames (e.g., `1_Upload_Submittal.py` → "Upload Submittal").

**Custom navigation via session state:**
All pages are imported as modules in `main.py`. A sidebar button sets `st.session_state.page`. The entry script routes to the correct `render()` function based on the page key.

**Why native multi-page routing was rejected:**

The system has a flow with hard gates: "Review Progress", "Report", and "Query Mode" must be disabled (not just hidden — actually unclickable) until a review has completed. Streamlit's native multi-page sidebar generates all page links unconditionally. There is no official way to disable individual page links in the native navigation.

Workarounds exist (hide pages from navigation via `st.set_page_config` tricks, or use CSS to style links as disabled) but they are fragile and version-dependent. The CSS hack that works in Streamlit 1.30 may break in 1.35.

More importantly: the native multi-page approach runs each page file as an independent script. `st.session_state` is shared between pages, but the import structure is different — each page cannot import functions from other pages easily. Our architecture has `app/pages/upload.py` importing from `src/agents/doc_processor.py`, which in turn has complex dependencies. This works fine when pages are imported as modules inside `main.py`; it would also work with native multi-page routing, but the `sys.path.insert` fix would need to be in every page file, not just `main.py`.

**Decision: custom navigation via `st.session_state.page`.**

Each page file exposes a single `render()` function. `main.py` calls the correct `render()` based on session state. Sidebar buttons for gated pages have `disabled=True` until `review_complete` is True.

Benefits:
- Full control over which buttons are enabled
- `sys.path.insert` needed only in `main.py`
- All pages can import from each other if needed
- Easy to add/remove pages without renaming files

---

### Issue 5.6 — File I/O Scattered Across the Pipeline

**When it appeared:** During the analysis of production transition readiness in Phase 5 planning.

**What happened:**

When mapping out what would need to change to move from local disk to S3, it became clear that file reads were scattered. `doc_processor.py` called `extract_text_from_bytes()` directly with bytes. The classifier called `pdf_parser.extract_text_from_bytes()`. But if files were on S3 instead of disk, every one of these call sites would need to first download the file.

The concern: if 12 places in the code do `open(path, "rb").read()` or equivalent, then moving to S3 requires finding and changing all 12 places. One missed location means a production bug.

**Solution: single file I/O utility.**

`src/parsers/file_io.py` defines two functions:

```python
def load_pdf_bytes(path: str | Path) -> bytes:
    """Single choke-point for file reads. Swap body for S3 download in production."""
    return Path(path).read_bytes()

def save_upload(dest_dir: str | Path, filename: str, data: bytes) -> Path:
    """Single choke-point for saving uploaded files. Swap body for S3 upload in production."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / filename
    out.write_bytes(data)
    return out
```

The comment in each function states exactly what to change for the production transition. The production version of `load_pdf_bytes` would be:
```python
def load_pdf_bytes(s3_key: str) -> bytes:
    s3 = boto3.client("s3")
    return s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)["Body"].read()
```

One function change. Nothing else changes.

**What changed in code:**
- Created `src/parsers/file_io.py` with `load_pdf_bytes()` and `save_upload()`
- `upload.py` uses `save_upload()` for all submitted files
- `spec_manager.py` uses `save_upload()` for all spec files
- Pipeline code that previously called `Path(path).read_bytes()` directly should call `load_pdf_bytes()` — this is a gradual migration, not yet complete for all call sites

---

## Architectural Debates and Reasoning

---

### Debate A — ChromaDB Cloud vs Local PersistentClient

**The original plan:**
Use ChromaDB Cloud for production (scalable, multi-user, no infrastructure to manage). Use local `PersistentClient` only for experiments.

**Why the plan changed:**

1. Free tier has a 300-record limit. Any real spec PDF produces thousands of chunks. There is no engineering workaround — it is a product constraint.
2. The 0.x→1.x breaking schema change created ongoing migration risk for Cloud collections. Local SQLite databases can be patched directly; Cloud databases cannot be patched with a SQL command.
3. The experiment phase ran entirely on local `PersistentClient` with no issues. The production codebase was built on the same foundation.

**The production transition plan:**

The `_chroma()` factory function in each of the three ChromaDB-using modules returns a `PersistentClient`. To switch to ChromaDB Cloud (or any other vector database), change the return of this factory. Nothing else changes.

For a full cloud vector database (Pinecone, Weaviate, Qdrant, or ChromaDB Cloud paid), the transition looks like:

```python
# Current (local)
def _chroma():
    return chromadb.PersistentClient(path=CHROMA_PATH)

# Production (ChromaDB Cloud paid)
def _chroma():
    return chromadb.HttpClient(
        host=os.environ["CHROMA_HOST"],
        port=int(os.environ.get("CHROMA_PORT", 8000)),
    )
```

The collection API (`get_or_create_collection`, `add`, `query`, `get`) is identical between local and cloud clients.

**Lesson:**
Defer infrastructure decisions until they are needed. A local database that works correctly is better than a cloud database that hits quota limits. The abstraction layer (`_chroma()` factory function) ensures the deferral is cost-free.

---

### Debate B — Production Transition Readiness of Local-First Architecture

**The question asked:**
If we build the prototype entirely locally (ChromaDB on disk, knowledge stores as JSON files, PDFs saved to `data/`), will the transition to production be smooth or a rewrite?

**The answer:**
Smooth, with specific seams to maintain. The architecture as built has these properties:

| Component | Local | Production Swap | Difficulty |
|---|---|---|---|
| Vector DB | `PersistentClient(path=CHROMA_PATH)` | `HttpClient(host=...)` or cloud SDK | 1 line per file |
| Knowledge store | `data/knowledge_stores/{id}.json` | PostgreSQL table or S3 object | 2 functions: `save()` and `load_store()` |
| Uploaded files | `data/submittals/{id}/` | S3 prefix `submittals/{id}/` | 1 function: `save_upload()` |
| Spec PDFs | `data/specs/adm/` | S3 prefix `specs/adm/` | 1 function: `save_upload()` |
| PDF reading | `Path(path).read_bytes()` | `s3.get_object(...)["Body"].read()` | 1 function: `load_pdf_bytes()` |
| Agents | Pure Python, no storage | No change | Zero |
| RAG pipeline | No change | No change | Zero |
| LangGraph graph | No change | No change | Zero |
| Streamlit UI | No change | No change | Zero |

**Things that would need new work (not rewrites):**
1. Authentication — currently no auth. Production needs login, RBAC, and session management
2. Multi-tenancy — `user_id` and `tenant_id` fields are pre-allocated in `SubmittalMetadata` but not populated. Add them when adding auth
3. Concurrent ChromaDB access — `PersistentClient` uses a file lock (single-process only). Production needs `HttpClient` (ChromaDB server mode) or a cloud client that handles concurrency natively
4. Process isolation — LangGraph runs in the same Streamlit process. Under load, one heavy review can block the UI for other users. Production would use a task queue (Celery, RQ) with a separate worker process

None of these require rewriting any business logic. All changes are at infrastructure boundaries.

---

## Future Known Issues and Watch Points

These are not current bugs but are known risks that should be addressed before any production deployment.

**1. Submittal RAG (Query Mode) — Collection Not Being Created**

The `src/rag/submittal_rag/embedder.py` exists and the ChromaDB collection schema is defined in `submittal_rag/store.py`, but nothing in the current review pipeline calls the embedder after the review completes. This means the `submittal_rag` query route in the Query Agent will always return "No relevant content was found" because the per-session collection is never populated.

For the prototype, the Query Agent still works for `spec_rag` and `report_json` routes, which cover the most important questions. The `submittal_rag` route would enable questions like "What did the manufacturer's datasheet say about pH?" that require searching the submitted documents themselves.

Fix: after `review_complete` is set to True in `review.py`, call `embed_submittal_documents()` with the knowledge store's sections to populate the submittal collection.

**2. BM25 Cache Staleness After Re-indexing**

`_build_bm25_for_network()` in `hybrid_retriever.py` uses `@lru_cache`. The cache is populated from ChromaDB at first query and held in memory for the process lifetime. If a new spec is indexed via Spec Manager while the Streamlit server is running, the BM25 cache does not see the new chunks until the process restarts.

Fix: add a "Clear BM25 Cache" button in Spec Manager that calls `_build_bm25_for_network.cache_clear()`. Or restart the Streamlit server after indexing (which is fine for the prototype, less acceptable in production).

**3. Single-Process Concurrency**

LangGraph runs synchronously in the Streamlit thread. One running review blocks the entire Streamlit app for that process. In local single-user use, this is invisible — one user runs one review at a time. In a shared deployment, it would block all users.

Fix (when needed): move the `graph.invoke()` call to a background thread via `threading.Thread` or a task queue. Use `st.session_state` to poll for completion. The review architecture itself is fully thread-safe (no shared mutable state between reviews).

**4. Placeholder Nodes Not Yet Implemented**

Three graph nodes return empty findings lists as stubs:
- `_boq_drawing_node` — BOQ/drawing material type check
- `_statement_node` — Compliance statement audit
- `_others_node` — OTHERS section document review

These are real review stages that the CLAUDE.md specifies. They are not bugs — they are deferred work. The review pipeline is complete and produces a valid report without them. When implemented, they are drop-in replacements in the existing graph.

**5. PDF Rotation / Mirror Causing Empty OCR**

As documented in Phase 2 Experiment B, some scanned PDF pages are rotated sideways or upside down. Tesseract OCR returns garbled or empty text for these pages. The current `pdf_parser.py` does not attempt to correct rotation before OCR.

Tesseract has a `--psm 1` mode that performs automatic page segmentation and orientation detection. Adding `config='--psm 1 --oem 3'` to the `pytesseract.image_to_string()` call would handle most rotation cases automatically.

This is not implemented because Phase 2 showed the table auditor correctly returns 0 rows for garbled pages (rather than hallucinating values). The failure mode is a WARNING finding ("Could not extract table content from page") rather than a wrong finding. Acceptable for the prototype.

---

---

## Phase 5 (Continued) — Specification Verification Architecture Overhaul

This section documents a major second pass on the specification verification layer that happened after the initial Phase 5 build was functionally working. The review pipeline ran end-to-end without errors, but the quality of spec verification findings was poor in ways that only became visible when reading actual output reports. This phase of work was driven by careful analysis of real report output and cross-referencing with what Stage 9 (Table Auditor) was correctly finding.

---

### Issue 5.7 — Spec Verifier Had No Structured Requirement Model

**When it appeared:** After the first complete review run produced a report, when reading the spec verification findings section and comparing it with the table audit findings.

**What happened:**

The original `spec_verifier.py` was a single-phase agent that did the following:
1. Retrieved the authority spec clause via RAG
2. Retrieved the submitted spec copy (Index 2)
3. Sent both to GPT with a prompt asking: "compare these two documents, flag anything wrong"
4. GPT returned a list of free-text findings

This produced findings that were qualitatively vague — things like "The submitted specification copy appears to reference the correct clause" or "Tensile strength requirement appears to be satisfied based on the comparison table." These findings:

- Had no structured connection to specific requirements (no IDs, no source clauses)
- Could not be used for deterministic numeric checks
- Could not drive the recommendation logic in `report_compiler` (which needed to know which requirements were actually non-compliant vs. just under-evidenced)
- Produced duplicate effort: Stage 3 re-read documents that Stage 9 had already parsed
- Made the report look generic rather than requirement-specific

Meanwhile Stage 9 (Table Auditor) was correctly finding issues like "proposed 45 MPa satisfies minimum 40 MPa (PASS)" because it had structured table rows from `store.table_rows`. Stage 3 had no equivalent.

**The root question that changed the design:**

> What does spec verification actually mean in this domain?

The answer: it does not mean "compare two documents." It means "extract requirements from the authority spec, then check whether the submitted documents satisfy each requirement." The spec is the source of truth. The submitted documents are evidence. Each requirement either has sufficient evidence or it does not.

This is a requirement-centric architecture, not a document comparison architecture.

**What changed:**

A full rewrite of `spec_verifier.py` and the creation of `src/models/requirements.py` as the canonical data model for the new approach.

**`src/models/requirements.py` — New file, created from scratch:**

- `RequirementType` enum — 13 types: `dimension`, `standard`, `material`, `test`, `certificate`, `approval`, `performance`, `installation`, `experience`, `administrative`, `warranty`, `procedural`, `other`
- `VerificationStatus` enum — `satisfied`, `non_compliant`, `partially_verified`, `missing_evidence`, `not_applicable`
- `VerificationMethod` enum — `numeric_comparison`, `string_match`, `semantic_match`, `certificate_validation`, `date_validation`, `llm_reasoning`
- `ExpectedValue` — structured value model with `operator` (≥, ≤, ==, in_range), `numeric_min`, `numeric_max`, `unit`, and a `.check(actual: float) -> bool` method for deterministic Python comparison
- `EvidenceExpectation` — `required_sources` (ALL must provide evidence), `optional_sources`, `minimum_optional_matches`
- `SpecRequirement` — full requirement model with `id`, `requirement_type`, `normalized_requirement`, `expected_value`, `evidence_expectation`, `source_clause`, `source_page`, `mandatory`, `comparison_table_required`
- `ReviewRequirementsArtifact` — extraction output (Phase 2 of spec verifier)
- `EvidenceSnippet` — a piece of text from a submitted document that supports a requirement
- `RequirementVerification` — per-requirement compliance result
- `RequirementVerificationArtifact` — all verifications for a review, with computed properties `satisfied_count`, `non_compliant_count`, `missing_evidence_count`, `mandatory_failures`

**`src/agents/spec_verifier.py` — Complete rewrite into three phases:**

*Phase 1 — Validate Index 2:*
Check that the submitted specification copy is present and references the correct clause number. Pure Python, no LLM. Produces `Finding` objects.

*Phase 2 — Extract Requirements:*
Call GPT to parse the retrieved authority spec context and produce a list of `SpecRequirement` objects. The LLM determines `evidence_expectation` from the spec wording, not from a hardcoded type→evidence table. The extraction prompt includes explicit rules for operator inference, evidence source rules, and `comparison_table_required` classification.

*Phase 3 — Verify Requirements:*
Call GPT to search the submitted documents for evidence of each requirement. Python then applies deterministic overrides: for numeric requirements, `expected_value.check(actual)` is called with confidence 1.0; for standard/material/certificate requirements, a regex string match is run at confidence 0.85.

**Two new state fields added to `src/agents/state.py`:**
- `requirements_artifact: dict` — `ReviewRequirementsArtifact.model_dump()` from Phase 2
- `verification_artifact: dict` — `RequirementVerificationArtifact.model_dump()` from Phase 3

These are consumed by both `table_auditor` (to filter which requirements appear in the comparison table) and `report_compiler` (to drive the recommendation logic).

**`src/agents/report_compiler.py` — Updated:**
- Added `_load_verification_artifact()` to read `RequirementVerificationArtifact` from state
- Added `_build_requirement_digest()` to produce a per-requirement PASS/FAIL/PARTIAL/MISSING digest for the summary LLM call
- Updated `_determine_recommendation()` to use structured compliance status:
  - Any `NON_COMPLIANT` requirement → RESUBMIT
  - `MISSING_EVIDENCE` + warnings → RESUBMIT
  - `MISSING_EVIDENCE` alone → CONDITIONAL
  - Otherwise falls back to raw finding severity counts

**`src/rag/query/context_assembler.py` — Added enriched retrieval:**
Added `assemble_spec_context_enriched()` — a non-cached variant that takes `material_description` and a `spec_snippet` (first 400 chars of the submitted spec copy) and appends them to the query embedding. This biases semantic search toward the specific subsections the contractor actually referenced, rather than the broadest possible match for the clause number alone.

**`src/agents/table_auditor.py` — Updated to use structured requirements:**
- `_build_requirements_json()` serializes `SpecRequirement` objects as structured JSON objects (not English text) for the audit prompt. The LLM receives clean data — `id`, `type`, `description`, `operator`, `value_min`, `unit` — not a paragraph it must re-parse
- `table_auditor_node()` filters requirements to `comparison_table_required=True` before auditing, so installation/experience/administrative requirements never appear as "missing" from the comparison table

**Lesson:**
A spec verifier that compares two documents is doing the wrong thing. The authority spec is not being compared to the submitted spec copy — it is providing the requirement list that the entire submittal must satisfy. The submitted spec copy is just one piece of evidence among many. The correct architecture is: extract requirements → gather evidence → verify each requirement. This is what professional engineers do. The AI should do the same.

---

### Issue 5.8 — Pydantic ValidationError: Boolean Fields Returning Null

**When it appeared:** During a review run after the table auditor rewrite. The same submittal that had passed on the previous build now crashed in the middle of Stage 8.

**Error message:**
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for TableRowFinding
proposed_verified
  Input should be a valid boolean [type=bool_type, input_value=None, input_url=...]
```

**What happened:**

The LLM was returning `null` for some boolean fields in the JSON response — specifically `proposed_verified` and `measured_verified`. These are valid JSON values for Python `None`.

The code that read these fields was:
```python
proposed_verified=raw.get("proposed_verified", False),
```

The bug: `dict.get(key, default)` only uses the `default` when the key is **absent** from the dict. When the key is present but the value is `None`, `.get()` returns `None` — not the default. `TableRowFinding` has `proposed_verified: bool` with no `Optional`, so Pydantic rejected `None` with a validation error.

This had not appeared in earlier test runs because earlier versions of the prompt produced valid booleans consistently. The new, longer prompt with more context produced null for some rows where the LLM was uncertain.

**Fix:**

Added a small helper function inside `_audit_batch()`:
```python
def _b(val: object, default: bool) -> bool:
    return val if isinstance(val, bool) else default
```

Applied to every boolean field assignment:
```python
proposed_verified=_b(raw.get("proposed_verified"), False),
measured_verified=_b(raw.get("measured_verified"), True),
```

This handles `None`, missing key, wrong type (string, int) all the same way — any non-bool falls back to the default. The defaults are chosen conservatively: `measured_verified=True` because an empty measured cell means "not tested" not "failed."

**Lesson:**
When reading LLM JSON output, never assume boolean fields are boolean. GPT frequently returns `null` for fields it is uncertain about. `dict.get(key, default)` is not sufficient — `isinstance(val, bool)` is required to distinguish an absent key from an explicitly null value.

---

### Issue 5.9 — Stage 3 Reported Missing Evidence That Stage 9 Already Found

**When it appeared:** After running a complete review and reading the report. Stage 8 (Table Auditor) was producing PASS findings for tensile strength, elongation, softening point, and thickness. Stage 3 (Spec Verifier) was simultaneously producing WARNING findings for the same parameters, saying "missing evidence from technical_datasheet and test_report."

**What happened — the root cause:**

Stage 9 reads `store.table_rows` — pre-parsed structured rows with columns `parameter`, `specified`, `proposed`, `measured`. These are extracted by `doc_processor` during document processing and stored in the knowledge store as structured data. Stage 9 never reads raw PDF text at all. It has direct access to the measured and proposed values as strings like `"1130 N/50mm"`.

Stage 3 only had access to raw PDF text, truncated to 2500 characters per document type. A test report with 15 pages has most of its content — including measured values on pages 4–10 — cut off after 2500 chars. The LLM in Stage 3 was looking at the front matter of the test report (lab name, certificate header, standard references) and never seeing the actual test results.

This was not a knowledge store problem. The full text was in `store.get_text(DocType.TEST_REPORT)`. The truncation happened in `_build_evidence_block()` after reading from the store.

**Three fixes applied:**

*Fix 1 — Include comparison table rows in Stage 3's evidence block.*

`_build_evidence_block()` was updated to include a `[COMPARISON_TABLE_ROWS]` section at the top of the evidence block, formatted identically to what Stage 9 uses:
```
[COMPARISON_TABLE_ROWS] pre-parsed structured data
  Tensile Strength: specified='500 N/50mm' | proposed='1000 N/50mm' | measured='1130 N/50mm'
  Elongation at break: specified='≥ 50%' | proposed='800%' | measured='52%'
  ...
```
Stage 3 now has access to the same data Stage 9 has. The LLM can cite these as `comparison_table` evidence.

*Fix 2 — Increase `_MAX_DOC_CHARS` from 2500 to 8000.*

8000 characters is approximately 4–5 pages of a typical test report. This does not solve the truncation problem for very long documents, but it covers the majority of real submittals (test reports with 3–8 pages of results).

*Fix 3 — Always include TEST_REPORT in the evidence block.*

Previously `always_include` was `{DocType.TECHNICAL_DATASHEET, DocType.COMPARISON_TABLE}`. Updated to also always include `DocType.TEST_REPORT`, regardless of what `evidence_expectation` specifies for any particular requirement.

**Lesson:**
When two stages in the same pipeline disagree about the same fact, the first question to ask is "do they read the same data source?" In this case they did not. Stage 9 read structured pre-parsed rows; Stage 3 read truncated raw text. The fix was to give Stage 3 access to the same structured data.

---

### Issue 5.10 — Wrong Operators Extracted for Performance Requirements

**When it appeared:** During analysis of Stage 3 output. A requirement for "Tensile Strength: 500 N/50mm" was being extracted with `operator: "=="` and `numeric_min: 500`. A product with tensile strength 1000 N/50mm was failing the check because `1000 == 500` is False.

**What happened:**

The extraction prompt had no explicit guidance on when to use `==` vs `>=`. GPT was defaulting to `==` for plain numeric values without a qualifying word ("minimum", "not less than", "≥"), treating them as exact requirements.

This is wrong for engineering specifications. A performance value listed without a qualifier always means a minimum. A product exceeding the stated value is always acceptable unless an upper limit is separately specified. `==` in an engineering spec means "exactly this value and nothing else" — which almost never appears in construction material specifications.

**Fix:**

Added explicit OPERATOR INFERENCE RULES to `_EXTRACT_SYSTEM`:
```
"minimum", "not less than", "≥", "at least"  → operator: ">="
"maximum", "not more than", "≤", "shall not exceed"  → operator: "<="
"between X and Y", "X to Y", "from X to Y"  → operator: "in_range"
"exactly", "shall be exactly"  → operator: "=="
Plain performance value with no qualifier (e.g. "Tensile Strength: 500 N"):
  → default to ">=" — NEVER use "==" unless explicitly required.
  A product exceeding a minimum is always acceptable.
```

**Lesson:**
LLMs will guess when they have no explicit rule. The guess for a numeric value with no qualifier is `==` because that is the most literal reading of an equality. Engineering context says `>=`. Always encode domain-specific conventions explicitly in the prompt — do not assume the LLM will infer them.

---

### Issue 5.11 — Installation Requirements Expected Evidence From Technical Datasheet

**When it appeared:** Stage 3 was extracting requirements for installation procedures (e.g., "Tape shall be laid 300 mm above pipeline") and setting `required_sources: ["technical_datasheet"]`. Since no manufacturer datasheet describes installation depth, this produced MISSING_EVIDENCE for every installation requirement.

**What happened:**

The LLM was inferring evidence sources from `required_sources` without domain-specific guidance. For any requirement that sounded product-related, it defaulted to `technical_datasheet`. Installation requirements are not product properties — they describe how to install, which belongs in a method statement, not a datasheet.

**Fix:**

Added explicit EVIDENCE SOURCE RULES by requirement type to `_EXTRACT_SYSTEM`:
```
installation ("laid X mm above", "backfill", "overlap"):
  required_sources: ["method_statement"]
  optional_sources: []
  DO NOT set technical_datasheet — a datasheet never describes installation procedures

warranty:
  required_sources: ["manufacturer_guarantee"]

administrative, procedural:
  required_sources: []
  optional_sources: [], minimum_optional_matches: 0
```

Also added the KEY PRINCIPLE:
> For most product properties, EITHER the technical datasheet OR the test report can serve as evidence. Do NOT set only one source as required unless the spec explicitly mandates it.

**Lesson:**
Evidence source inference is not a general-purpose task — it requires domain knowledge about what type of document contains what type of information. Installation procedures are never in datasheets. Warranties are never in test reports. This knowledge must be encoded explicitly. LLMs have construction domain knowledge but may not apply it consistently without rules.

---

### Issue 5.12 — Contradiction Detection Threshold Too Aggressive (5%)

**When it appeared:** Stage 9 (Table Auditor) was flagging rows as CONTRADICTION where the datasheet said 1000 N/50mm and the test report measured 1130 N/50mm — a 13% difference. The finding was "CONTRADICTION: datasheet shows 1000, test report shows 1130 (13% difference)."

**What happened:**

The contradiction detection threshold was set at 5%:
```python
if diff_pct > 0.05:
    raw["contradiction_detected"] = True
```

In construction material testing, several legitimate sources of variation routinely produce 5–15% differences between a manufacturer's declared value and an independent test result:
- The datasheet states a **typical** value; the test report measures an **actual** sample
- Test conditions differ (temperature, humidity, specimen preparation)
- The manufacturer's value may be a minimum; the tested specimen exceeded it

A 13% difference between declared 1000 N/50mm and measured 1130 N/50mm is not a contradiction — it is a compliant product that performed better than declared.

**Fix:**

Threshold changed from 5% to 15%:
```python
if diff_pct > 0.15:
    raw["contradiction_detected"] = True
```

Updated comment explains the reasoning so future developers do not revert it without understanding why it was changed.

**Lesson:**
Domain knowledge must drive threshold choices. A 5% threshold is appropriate for financial data or sensor readings. For construction material testing, manufacturing tolerances and test condition variability routinely produce 5–10% variation that has no engineering significance. Always ask "what does this number mean in the domain?" before setting a threshold.

---

### Issue 5.13 — `comparison_table_required` Extracted But Never Read

**When it appeared:** After implementing `comparison_table_required` as a field in `SpecRequirement`, installation and administrative requirements were still appearing as "missing from comparison table" in Stage 9's output.

**What happened:**

The extraction prompt was correctly returning `comparison_table_required: false` for installation requirements. But `_phase2_extract_requirements()` was constructing `SpecRequirement` objects without reading this field from the LLM response:

```python
req = SpecRequirement(
    id=item.get("id", ...),
    ...
    mandatory=item.get("mandatory", True),
    # comparison_table_required was not here — it defaulted to True for everything
)
```

The LLM was doing the right thing. The parsing code was silently discarding the result. Every requirement defaulted to `comparison_table_required=True`, causing Stage 9 to flag every installation requirement as "missing from the comparison table."

**Fix:**
```python
req = SpecRequirement(
    ...
    mandatory=item.get("mandatory", True),
    comparison_table_required=item.get("comparison_table_required", True),
)
```

One line. The `table_auditor_node` already had the filter in place:
```python
table_reqs = [r for r in requirements_artifact.requirements if r.comparison_table_required]
```
It just wasn't getting the right value because parsing was incomplete.

**Lesson:**
When adding a new field to a Pydantic model AND the LLM extraction prompt, always trace the full path: prompt → LLM response JSON → parsing code → model constructor → downstream usage. Any break in this chain causes a silent failure. The field exists in the model, the prompt extracts it, the LLM returns it — but if the parsing code doesn't read it, the default wins.

---

### Issue 5.14 — Deterministic Override Only Ran If LLM Found Evidence First

**When it appeared:** During architectural review of `spec_verifier.py` after Stage 3 was still producing `missing_evidence` for some numeric requirements that Stage 9 found correctly.

**What happened:**

The deterministic override in `_apply_deterministic_overrides()` worked as follows:
1. Look at `v.evidence_found` for `extracted_value`
2. If found → run `expected_value.check(actual)`
3. Override the status

But if the LLM returned `evidence_found = []` — meaning it failed to locate the value in the prompt text — then `numeric_values = []`, `actual = None`, and the override never ran. The verification stayed at `missing_evidence` even though the value was available in `store.table_rows`.

This meant that Stage 3's numeric verification still had a single point of failure: if GPT missed the value in the prompt, Python never got a chance to check. Stage 9 had no such dependency — it read from `store.table_rows` directly without asking GPT to search anything.

**The architectural insight:**

Stage 9 is reliable not because its prompt is better, but because its data path is different:
```
Stage 9: store.table_rows → Python extracts value → Python checks → PASS/FAIL
Stage 3: store.table_rows → build_evidence_block → prompt → GPT searches → extracts value → Python checks
```
Stage 3 has GPT as an intermediate step that can fail. Stage 9 does not.

**Fix — Two-pass deterministic override:**

`_apply_deterministic_overrides()` was updated to accept `table_rows` as an optional parameter and implement a second pass:

```
Pass 1: Use extracted_value from LLM's evidence_found (if present)
Pass 2: If evidence_found is empty, search store.table_rows directly with Python
        → _match_table_row() finds the row, extracts the numeric value
        → Synthetic EvidenceSnippet created with document_type=comparison_table
        → expected_value.check(actual) runs regardless of what GPT returned
```

`_match_table_row()` uses `rapidfuzz.fuzz.token_set_ratio()` to match requirement names to table row parameters:
- "Minimum tensile strength ≥ 500 N" matches "Tensile Strength" (score ~85)
- "Peel Strength" does NOT match "Tensile Strength" despite sharing "strength" (score ~45, below 60 threshold)
- Threshold: 60 — empirically chosen to catch legitimate matches while avoiding single-word false positives

Evidence priority ordering was also fixed. Previously `numeric_values[0]` picked whichever evidence snippet the LLM returned first — which was LLM-order-dependent. Now evidence snippets are sorted by source quality before picking `actual`:
```
test_report (3) > comparison_table (2) > technical_datasheet (1)
```
If both a test report and a datasheet mention a value, the test report value is used (it is independently measured; the datasheet is manufacturer-declared).

**Lesson:**
A deterministic override that depends on the LLM to find the evidence first is not truly deterministic. The Python fallback must be able to find evidence independently of what the LLM returned. For numeric requirements, `store.table_rows` is the canonical structured evidence source — not the raw document text and not the LLM's search results.

---

### Issue 5.15 — Prompt Instructed GPT to Do Numeric Comparison (Python's Job)

**When it appeared:** During architectural review of `_VERIFY_SYSTEM`.

**What happened:**

The verification prompt told GPT:
```
- numeric: find the value in ANY document, report extracted_value, assess against operator
```

But Python was overriding the assessment anyway via `_apply_deterministic_overrides()`. GPT was doing work that Python would immediately discard. Worse, GPT's assessment could pollute the `status` field before the override ran, and if the override didn't run (e.g., `evidence_found` was empty), GPT's wrong assessment became the final result.

**Fix:**

Changed the prompt instruction to cleanly separate responsibilities:
```
- numeric: find the value in ANY document, report extracted_value — Python performs the
  pass/fail evaluation after you return. Your job is only to locate the value and report
  extracted_value accurately.
```

GPT is now responsible for finding and reporting the value. Python is responsible for evaluating it. Clear separation of concerns.

**Lesson:**
When an LLM's output in one field will always be overridden by deterministic Python code, remove the instruction that asks the LLM to produce that output in the first place. Redundant LLM computation costs tokens, can introduce confusion, and creates a "two sources of truth" situation where the wrong one might win if the override fails.

---

### Issue 5.16 — Standard and Material Requirements Left to LLM Judgment

**When it appeared:** During architectural review of `_apply_deterministic_overrides()`. After fixing numeric overrides, standard/material/certificate requirements were still fully LLM-determined — GPT decided whether "BS EN 13252" appeared in the documents.

**What happened:**

Standard code references like "BS EN 13252", "ASTM D638", material grades like "HDPE PE100", and certifications like "NSF 61" are exact strings. If they appear anywhere in the submitted documents, the requirement is satisfied. This does not require semantic reasoning — it requires a regex search.

GPT is reliable for this in most cases but introduces unnecessary variability. A deterministic regex is faster, cheaper, and produces exactly reproducible results.

**Fix — `_apply_text_overrides()`:**

Added a third deterministic pass after `_apply_deterministic_overrides()`, targeting `RequirementType.STANDARD`, `RequirementType.MATERIAL`, and `RequirementType.CERTIFICATE`:

1. Extract search terms from `expected_value.text` (e.g., "BS EN 13252")
2. Split into tokens; try full phrase first, then individual tokens
3. Skip tokens ≤ 2 chars or pure digits (to avoid false positives from "BS", "EN", "61")
4. `re.search(re.escape(candidate), evidence_text, re.IGNORECASE)`
5. If found → override to `string_match`, `satisfied`, confidence 0.85
6. If not found → leave LLM result unchanged (LLM may have found a semantic equivalent)

The search runs against the full `evidence_block` string — the same text the LLM already received, so no extra I/O.

**Confidence levels:**
- Numeric comparison: 1.0 (deterministic math)
- String match (standard/material/certificate): 0.85 (string found, but context not verified)
- LLM reasoning (semantic): 0.50–0.90 (model-dependent)

**Coverage after all three override layers:**
```
Numeric (dimension, performance, test): Python math, confidence 1.0
Standard code, material grade, certification: regex, confidence 0.85
Installation, experience, administrative: LLM reasoning only (these are inherently semantic)
```

**Lesson:**
Classify verification tasks by their information-theoretic nature before assigning them to an LLM. Text presence is a search problem, not a reasoning problem. Numeric comparison is math, not reasoning. LLMs should only be used for tasks that genuinely require understanding — conflicting wording, ambiguous context, semantic equivalence. Everything else should be deterministic.

---

## Architectural Debates and Reasoning (Continued)

---

### Debate C — Evidence Architecture: Document Comparison vs. Requirement-Centric

**The original design:**
Spec verifier receives spec context + submitted documents → GPT compares them → produces findings.

**The problem with this design:**

A document comparison approach answers "Do these two documents agree?" But the right question is "Does the submitted evidence satisfy each requirement from the authority specification?"

These are different questions with different answers. Two documents can "agree" while both failing a requirement. A submitted spec copy that says "tensile strength 500 N/50mm" agrees with the authority spec that says "tensile strength ≥ 500 N/50mm" — but the datasheet needs to confirm the product actually achieves this value.

**The requirement-centric design:**

1. The authority spec is the source of truth → extract structured requirements from it
2. Submitted documents are evidence → search them for each requirement
3. Each requirement either has sufficient evidence (satisfied) or does not (missing_evidence, non_compliant, partially_verified)
4. Python applies deterministic checks wherever possible — LLM only handles semantic reasoning

This mirrors how a professional engineer actually reviews a submittal. They read the spec clause, note what is required, then check each submitted document against those requirements. They do not "compare two documents."

**Why the rewrite was worth it:**

The old design produced generic, uncheckable findings. The new design produces:
- Findings tied to specific requirement IDs
- Numeric findings with deterministic PASS/FAIL at confidence 1.0
- A `RequirementVerificationArtifact` that drives recommendation logic in `report_compiler`
- Structured output that can be queried in post-review chat mode
- A `comparison_table_required` flag that prevents Stage 9 from generating false "missing" findings for non-table requirements

---

### Debate D — Where Should Numeric Verification Live?

**The question:** Should numeric requirement checking happen in Stage 3 (Spec Verifier) or Stage 9 (Table Auditor)?

**What actually happens today:**
Both stages do numeric verification, but from different data sources:
- Stage 3 uses `store.table_rows` (via Python fallback `_match_table_row`) and `expected_value.check(actual)` from `RequirementVerification`
- Stage 9 also uses `store.table_rows` and `expected_value.check(actual)` from `TableRowFinding`

This creates a situation where both stages produce verdicts on the same numeric value. If the two verdicts agree, the report has a consistent story. If they disagree (Stage 3 says PASS, Stage 9 says WARNING), the engineer reading the report has contradictory information.

**Why this duplication exists:**
Stage 3 is the requirement authority — it owns the `RequirementVerificationArtifact` that drives `report_compiler`'s recommendation. Stage 9 is the table auditor — it reviews each row of the contractor's comparison table and flags incorrect specified values, unverifiable proposed values, and missing parameters. They answer related but different questions:
- Stage 3: "Does the submitted package satisfy requirement R-003?"
- Stage 9: "Is row 5 of the comparison table correct and verifiable?"

They are intended to be complementary, not duplicative. Row 5 might say "Specified: 500 N | Proposed: 1000 N | Measured: 1130 N" — Stage 9 verifies the row structure; Stage 3 verifies that R-003 (tensile ≥ 500 N) is satisfied.

**Current risk:** If Stage 3's `_match_table_row()` fuzzy-matches a requirement to the wrong table row, the numeric check uses the wrong value. Stage 9 would not catch this because it audits rows, not requirements.

**Resolution:** This is a known architectural tension, not a bug. It will resolve naturally when the Submittal RAG is implemented — Stage 3 will retrieve evidence chunks per requirement rather than searching table rows, and Stage 9 will remain the table structure auditor. See Future Issue 6 below.

---

## Future Known Issues and Watch Points (Updated)

These are not current bugs but are known risks and planned improvements, prioritized by impact.

**1. Submittal RAG — Per-Requirement Chunk Retrieval (Highest Priority)**

Currently `store.get_text(doc_type)` returns concatenated raw text from all pages of a document type, truncated to 8000 characters. For a 40-page test report, evidence on pages 10–40 is never seen by Stage 3.

The proper fix is `store.get_relevant_chunks(doc_type, query=req.normalized_requirement)` — embed the submitted document pages into a per-session ChromaDB collection and retrieve the top 3–5 chunks most relevant to each requirement.

This would:
- Eliminate the 8000-char truncation problem
- Scale to 100-page technical documents without increasing prompt size
- Give Stage 3 the same retrieval quality Stage 9 already has for comparison table data
- Enable truly per-requirement evidence lookup

Blocked by: `src/rag/submittal_rag/embedder.py` exists but nothing in the pipeline calls it after a review. Fix: after `review_complete`, call `embed_submittal_documents()` to populate the per-session collection. Then replace `_build_evidence_block()` with per-requirement chunk retrieval.

**2. BM25 Cache Staleness After Re-indexing**

`_build_bm25_for_network()` in `hybrid_retriever.py` uses `@lru_cache`. The cache is populated at first query and held for the process lifetime. If a new spec is indexed via Spec Manager while the Streamlit server is running, the BM25 cache does not see the new chunks until the process restarts.

Fix: add a "Clear BM25 Cache" button in Spec Manager that calls `_build_bm25_for_network.cache_clear()`.

**3. Single-Process Concurrency**

LangGraph runs synchronously in the Streamlit thread. One review blocks the entire app. For a shared deployment, this blocks all users.

Fix: move `graph.invoke()` to a background thread or task queue. The review architecture itself is thread-safe — no shared mutable state between reviews.

**4. Placeholder Nodes Not Yet Implemented**

Three graph nodes return empty findings lists as stubs:
- `_boq_drawing_node` — BOQ/drawing material type check
- `_statement_node` — Compliance statement audit
- `_others_node` — OTHERS section document review

These are real review stages. They are deferred work, not bugs. Drop-in replacements in the existing graph.

**5. PDF Rotation / Mirror Causing Empty OCR**

Some scanned PDF pages are rotated sideways or upside down. Tesseract returns garbled or empty text. Current failure mode is a WARNING finding ("Could not extract table content") rather than a wrong finding — acceptable for the prototype.

Fix: add `config='--psm 1 --oem 3'` to `pytesseract.image_to_string()` for automatic orientation detection.

**6. Stage 3 and Stage 9 Numeric Verification Overlap**

Both stages verify numeric requirements from the same `store.table_rows` data. This produces consistent results when both checks use the same value, but creates potential for contradictory report findings if `_match_table_row()` fuzzy-matches a requirement to the wrong row.

Fix: once the Submittal RAG is implemented (Future Issue 1), Stage 3 will retrieve evidence chunks from embedded document pages rather than searching table rows. Stage 9 remains the comparison table structure auditor. The architectural roles become cleanly separated.

**7. Evidence Provenance for Synthetic Snippets**

When `_match_table_row()` finds a value and creates a synthetic `EvidenceSnippet`, it records `page=0` and `source_document="comparison_table (structured rows)"`. The actual page number and original PDF filename are not preserved because `store.table_rows` dicts do not carry this metadata.

Fix: when `doc_processor` stores table rows in the knowledge store, also store the source page and filename for each row. Then `_match_table_row()` can populate `EvidenceSnippet.page` and `EvidenceSnippet.source_document` with real values, enabling precise citation in the report.

**8. Prompt Size Will Become a Constraint**

Stage 3 currently sends all requirements + all document text in one prompt. For a spec clause with 20+ requirements and three 8000-char documents, the combined prompt is 25,000–30,000 tokens. `gpt-4o-mini` supports 128k context so this is within limits today, but there is no batching logic for very large requirement sets.

Once Submittal RAG is implemented (Future Issue 1), per-requirement chunk retrieval will replace the single large evidence block with small targeted chunks. The prompt size problem largely solves itself at that point. Until then, if a spec clause produces 30+ requirements, the prompt could degrade in quality due to attention dilution over a very long context.

---

*Engineering log started: 2026-06-24*
*Covers: Phase 3 (Production Build) · Phase 4 (Scenario Testing) · Phase 5 (UI Build + Spec Verification Overhaul)*
*Last updated: 2026-07-03*
*Next update: Phase 6 (Portfolio Polish) and Submittal RAG implementation*
