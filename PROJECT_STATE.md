# PROJECT_STATE.md

> Originally Phase 0 output — a factual inventory of the Streamlit prototype (§1-12 below,
> still accurate, `src/` untouched throughout). **Updated through Phase 3**: §13 documents
> the production layer built on top (`db/`, `migrations/`, `apps/worker/`, `apps/api/`) —
> Postgres schema + RLS, the job-queue worker, and the FastAPI + Cognito API. Kept current as
> we build, not a point-in-time snapshot — this file is the ground truth for what has
> actually been verified to work, not just written.

---

## 1. The real LangGraph entry point (build plan placeholder resolved)

The build plan references `get_graph()` as a placeholder. **The real name is:**

```python
from src.agents.orchestrator import compile_review_graph
graph = compile_review_graph()          # returns a compiled, runnable LangGraph app
result = graph.invoke(initial_state)     # or graph.stream(initial_state) for live progress
```

- `build_review_graph()` (same file) builds the uncompiled `StateGraph(SubmittalReviewState)`.
- `compile_review_graph()` calls `.compile()` on it and is what everything else imports
  (`app/pages/review.py`, `experiments/agents/scenario_tests.py`).
- `initial_state` must contain at minimum: `authority` ("ADM" | "TAQA"), `submittal_id`,
  `review_date` (defaults to today if omitted).
- **Before calling `.invoke()`/`.stream()`, the caller MUST call `stage_files(submittal_id,
  file_contents, declared_labels)`** (from `src.agents.doc_processor`) to deposit raw PDF bytes
  into a process-local staging dict. Bytes never enter graph state — see §5.

---

## 2. The node graph (actual wiring, from `src/agents/orchestrator.py`)

```
doc_processor → completeness → boq_drawing → spec_verifier → validity_checker
                                                                    ↓
                                                 ┌──────────────────┤
                                                 ↓ (TAQA)          ↓ (ADM)
                                              avl_check         skip_avl
                                                 └────────┬─────────┘
                                                           ↓
                                    statement → table_auditor → consistency → others
                                                                                  ↓
                                                                          report_compiler → END
```

Every node always runs; there is no early exit. Node functions merge state via
`{**state, "field": value}` — never mutate in place. Conditional routing:
`_should_run_avl(state)` returns `"avl_check"` if `state["authority"] == "TAQA"` else
`"skip_avl"`.

| Node key         | Function                                       | File                                  | Type            |
|-------------------|------------------------------------------------|----------------------------------------|-----------------|
| `doc_processor`   | `doc_processor_node`                           | `src/agents/doc_processor.py`          | LLM + OCR       |
| `completeness`    | `_completeness_node` (inline in orchestrator)  | calls `src/rules/completeness.py`      | Rule-based      |
| `boq_drawing`     | `_boq_drawing_node`                             | orchestrator.py                        | **Placeholder** — returns `{"boq_drawing_findings": []}` |
| `spec_verifier`   | `spec_verifier_node`                            | `src/agents/spec_verifier.py`          | LLM + RAG + deterministic override |
| `validity_checker`| `validity_checker_node`                        | `src/agents/validity_checker.py`       | Rule-based (dates) |
| `avl_check`       | `avl_checker_node`                              | `src/agents/avl_checker.py`            | Rule-based + fuzzy match, TAQA only |
| `skip_avl`        | `_skip_avl_node`                                | orchestrator.py                        | No-op, ADM path |
| `statement`       | `_statement_node`                               | orchestrator.py                        | **Placeholder** — returns `{"statement_findings": []}` |
| `table_auditor`   | `table_auditor_node`                            | `src/agents/table_auditor.py`          | LLM + deterministic override |
| `consistency`     | `consistency_checker_node`                      | `src/agents/consistency_checker.py`    | Rule-based fuzzy match |
| `others`          | `_others_node`                                  | orchestrator.py                        | **Placeholder** — returns `{"others_findings": []}` |
| `report_compiler` | `report_compiler_node`                          | `src/agents/report_compiler.py`        | Aggregation + LLM summary |

Post-review, a separate (non-graph) entry point exists: `handle_query(state, question)` in
`src/agents/query_agent.py`, invoked from `app/pages/chat.py` after `review_complete=True`.

---

## 3. Every agent — what it reads and writes

All agents read `state["knowledge_store_id"]` → `load_store(path)` (from
`src/models/knowledge_store.py`) to get the `SubmittalKnowledgeStore`. None except
`doc_processor` ever touch raw PDF bytes.

### Agent 1 — `doc_processor_node` (`src/agents/doc_processor.py`)
- **Reads:** raw PDF bytes via the module-level `_staging` dict (popped by `submittal_id`,
  deposited earlier by `stage_files()`).
- **Does:** classifies every uploaded file (GPT-4o-mini, 14-category prompt) with two paths —
  individual files, or a bundled PDF (≥20 pages, `_BUNDLED_THRESHOLD`) split via separator-page
  detection (Option A) or sparse sampling (Option B, step=3/window=3, early-stop after 5 steps
  with no new type). Extracts cover-page fields (material_description, spec_clause,
  manufacturer_name/address, supplier_name/address) via a second GPT-4o-mini call. Pre-parses
  ALL comparison table rows upfront via `extract_all_table_rows()` (OCR-first, pdfplumber
  fallback) so no later agent needs PDF bytes.
- **Writes:** `SubmittalKnowledgeStore` object → `store.save()` → `data/knowledge_stores/{submittal_id}.json`.
  State only receives `knowledge_store_id` (the file path string).
- **Module-level state:** `_staging: dict[str, tuple[dict[str,bytes], dict[str,str|None]]]`
  (bytes staging) and, in `knowledge_store.py`, a separate `_cache` dict so the JSON is read
  from disk at most once per process per submittal.

### Completeness — `_completeness_node` (inline in `orchestrator.py`, rule-based)
- **Reads:** `store.get_present_types()`, `store.get_mismatches()`, the authority profile
  (`get_authority_profile(authority)` from `src/config`).
- **Does:** compares present doc types against `profile.required_doc_types`; MAF and
  PREVIOUS_APPROVAL are treated as mutually substitutable for Index 8.
- **Writes:** `completeness_findings`, `missing_documents`.

### Agent 2 — `spec_verifier_node` (`src/agents/spec_verifier.py`) — 3 phases
- **Reads:** knowledge store (spec_clause, material_description, all document text, `table_rows`);
  calls into RAG Pipeline 2 via `assemble_spec_context_enriched()`.
- **Phase 1:** validates Index 2 (submitted spec copy) is present and references the right clause
  — pure Python, no LLM.
- **Phase 2:** retrieves spec text (hybrid retriever → RRF → Cohere rerank → parent-fetch),
  one GPT-4o-mini call extracts a list of `SpecRequirement` objects (13 requirement types,
  structured `ExpectedValue`, `EvidenceExpectation`, `comparison_table_required` flag).
- **Phase 3:** `_build_evidence_block()` assembles comparison-table rows + raw doc text
  (truncated 8000 chars/type); ONE GPT-4o-mini call verifies all requirements; then Python
  deterministic overrides — numeric (`ExpectedValue.check()`, two-pass fallback into
  `store.table_rows` via rapidfuzz `token_set_ratio` ≥60) and text (verbatim match for
  standard/material/certificate types, confidence 0.85).
- **Writes:** `requirements_artifact` (→ read by table_auditor), `verification_artifact`
  (→ read by report_compiler), `spec_verification_findings`.

### Agent 3 — `validity_checker_node` (`src/agents/validity_checker.py`, rules in `src/rules/date_checker.py`)
- **Reads:** all `DED_REGISTRATION`, `TEST_REPORT`, `MANUFACTURER_GUARANTEE` sections (full text).
- **Does:** pure Python regex date extraction + comparison. DED: expired → CRITICAL, expiring
  ≤30 days → WARNING. Test report: age >3 years (1095 days) → CRITICAL. Guarantee: period
  regex-extracted but never compared against a real required period (always `None` currently).
- **Writes:** `validity_findings`. No AI.

### Agent 6 — `avl_checker_node` (`src/agents/avl_checker.py`) — TAQA only
- **Reads:** sections classified `PREVIOUS_APPROVAL`/`OTHERS` containing "approved vendor"/
  "vendor list"; `store.manufacturer_name`.
- **Does:** fuzzy-matches manufacturer name (`rapidfuzz.token_sort_ratio` ≥85) against AVL text.
- **Writes:** `avl_findings`. Runs only if `authority=="TAQA"`; ADM takes `skip_avl` instead
  (writes `avl_findings: []`).

### Agent 4 — `table_auditor_node` (`src/agents/table_auditor.py`)
- **Reads:** `store.table_rows` (pre-parsed by Agent 1, never touches PDF bytes),
  `state["requirements_artifact"]` filtered to `comparison_table_required=True`.
- **Does:** one GPT-4o-mini call per batch of 25 rows; Python deterministic override for
  numeric rows (`ExpectedValue.check()`); contradiction detection when proposed vs measured
  differ >15%; synthesizes a standalone CRITICAL finding for any mandatory requirement absent
  from the table entirely.
- **Writes:** `table_audit_findings` (list of `TableRowFinding`).

### Agent 5 — `consistency_checker_node` (`src/agents/consistency_checker.py`, rules in `src/rules/name_matcher.py`)
- **Reads:** `store.manufacturer_name`, `store.supplier_name`, first 1000 chars of each
  relevant section's text.
- **Does:** `rapidfuzz.partial_ratio`/`token_sort_ratio` ≥85 fuzzy match. No AI.
- **Writes:** `consistency_findings`.

### Agent 7 — `report_compiler_node` (`src/agents/report_compiler.py`)
- **Reads:** every `*_findings` state key, `state["verification_artifact"]`.
- **Does:** counts criticals/warnings; recommendation logic (priority order):
  `critical_count>0 → RESUBMIT`; `non_compliant_count>0 → RESUBMIT`;
  `missing_evidence_count>0 AND warnings exist → RESUBMIT`;
  `missing_evidence_count>0 (alone) → CONDITIONAL`; `warning_count>2 → CONDITIONAL`;
  else `APPROVE`. One GPT-4o-mini call (temperature=0) writes a 2-4 sentence summary from a
  requirement-level digest.
- **Writes:** `report` (`ReviewReport.model_dump()`), sets `review_complete=True`.

### Agent 8 — `query_agent.handle_query(state, question)` (`src/agents/query_agent.py`) — NOT a graph node
- Called post-review from `app/pages/chat.py`. Router LLM call classifies the question into
  `spec_rag` (re-invokes Pipeline 2), `submittal_rag` (**dead path — see §6**), or
  `report_json` (flattens `state["report"]` to text). All answers grounded-only, must cite source.
- **Writes:** appends to `state["conversation_history"]` (list of `ConversationTurn` dicts).

### Placeholder nodes (execute, return empty lists, do nothing else)
`_boq_drawing_node` → `boq_drawing_findings: []`; `_statement_node` → `statement_findings: []`;
`_others_node` → `others_findings: []`.

---

## 4. RAG pipeline — `src/rag/`

### Indexing (`src/rag/indexing/`) — admin-only, run via `app/pages/spec_manager.py` or
`scripts/setup_chromadb.py` / `scripts/load_adm_spec.py` (currently a 1-line stub —
**not implemented**, same for `load_taqa_spec.py`).

`pdf_loader.py` (native+OCR page text, tags `authority`/`network`) →
`structurer.py` (groups pages into `SpecSection`s by clause-number regex) →
`chunker.py` (fixed 500-char chunks, 50-char overlap; oversized sections >6000 chars
sub-split first at `MAX_CLAUSE_CHARS`) →
`metadata_tagger.py` (8 metadata fields: authority, network, division, section, clause,
source_file, chunk_index, parent_id) →
`indexer.py` (embeds via OpenAI `text-embedding-3-small`, batches of 500, writes to ChromaDB).

### Query (`src/rag/query/`) — runs on every review, called from `spec_verifier.py` and
(via the shared `context_assembler`) `table_auditor.py`.

`query_constructor.py` (clause ref → `StructuredQuery` with network filter, looked up via a
`_CLAUSE_TO_NETWORK` map) →
`hybrid_retriever.py` (BM25Okapi top-20, network-filtered, index cached per
(collection,network); + semantic top-20 via ChromaDB `where={"network":...}`; Reciprocal Rank
Fusion, `k=60`) →
`reranker.py` (Cohere `rerank-english-v3.0`, top-20 → top-5) →
`parent_fetcher.py` (expands each top-5 chunk to its full parent `SpecSection` via `parent_id`
lookup + chunk_index reassembly) →
`context_assembler.py` — two entry points:
- `assemble_spec_context(clause_ref, authority, question=None)` — **cached** (`@lru_cache(maxsize=64)`
  keyed on `(normalize_clause_ref(clause_ref), authority)`), used by `query_agent.py`'s `spec_rag` route.
- `assemble_spec_context_enriched(clause_ref, authority, material_description, spec_snippet)` —
  **not cached**, used once per review by `spec_verifier.py` Phase 2 (biases retrieval with the
  contractor's own submitted spec-copy text).

If retrieval returns nothing, both functions return the literal string
`"__SPEC_NOT_FOUND__"` (`EMPTY_CONTEXT_SENTINEL`) rather than hallucinating.

### Current vector store
**ChromaDB, `PersistentClient` (local, SQLite-backed), on disk at `data/chromadb/`**
(path from `src/config/paths.py::CHROMA_PATH`). Two collections:
`adm_specifications`, `taqa_specifications`. `scripts/setup_chromadb.py` targets **ChromaDB
Cloud** instead (`chromadb.HttpClient`, uses `CHROMA_API_KEY`/`CHROMA_TENANT`/`CHROMA_DATABASE`
from `.env`) — this script is a leftover from an abandoned Cloud path (300-record free-tier
quota made it unusable) and is **not** what the running app actually uses.

### `src/rag/submittal_rag/` — exists, fully coded, but NEVER CALLED from the active pipeline
- `embedder.py::embed_submittal_documents()`, `store.py::store_embeddings()` /
  `create_submittal_collection()` / `collection_exists()` / `delete_submittal_collection()`,
  `retriever.py::retrieve_from_submittal()`.
- Would embed submittal document text into a per-submittal ChromaDB collection
  (`{submittal_id}` scoped, local `PersistentClient`) for the `submittal_rag` chat route.
- **No code path in `doc_processor.py` or elsewhere calls `embed_submittal_documents()`.**
  The `submittal_rag` route in `query_agent.py` will return "no relevant content found" for
  every real review until this is wired in.

---

## 5. The staging pattern and `file_io.py` (the two functions the build plan may touch)

**`stage_files()`** — `src/agents/doc_processor.py:31`
```python
_staging: dict[str, tuple[dict[str, bytes], dict[str, str | None]]] = {}

def stage_files(submittal_id, file_contents, declared_labels=None) -> None:
    _staging[submittal_id] = (file_contents, declared_labels or {})
```
Called by the UI (`app/pages/upload.py`) before `graph.invoke()`/`.stream()`. Popped by
`doc_processor_node` (`_staging.pop(submittal_id, ({}, {}))`) — first and only place PDF bytes
enter the pipeline. This is process-local, in-memory, single-process only.

**`src/parsers/file_io.py`** — the ONE file `CLAUDE.md` permits modifying in `src/`:
```python
def load_pdf_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()

def save_upload(dest_dir, filename, data: bytes) -> Path:
    dest = Path(dest_dir); dest.mkdir(parents=True, exist_ok=True)
    out = dest / filename; out.write_bytes(data)
    return out
```
`save_upload()` is called from `app/pages/upload.py` and `app/pages/spec_manager.py` to persist
uploaded bytes to `data/submittals/{submittal_id}/` (upload) or spec staging dirs before
indexing. `load_pdf_bytes()` is the read-side counterpart. Per CLAUDE.md, swapping these two
function bodies to S3 is the only sanctioned `src/` change outside this exercise.

---

## 6. Rules, parsers, models — inventory

### `src/rules/` (pure Python, no LLM)
- `completeness.py::check_completeness(present_types, mismatches, profile)` → `(findings, missing_doc_types)`.
- `date_checker.py::check_ded_registration()`, `check_test_report()`, `check_guarantee()` —
  5 regex date formats, expiry/age logic described in §3 Agent 3.
- `name_matcher.py` — rapidfuzz-based fuzzy name matching used by consistency checker and AVL checker.

### `src/parsers/`
- `pdf_parser.py` — `extract_text_from_bytes()`, `extract_page_text_from_bytes()`,
  `get_page_count()`, `is_separator_page()`. Native-text-first (PyMuPDF), OCR fallback
  (pytesseract, 2× zoom / ~144 DPI) when native text <50 chars/page.
- `classifier.py` — `classify_document()` (raw GPT-4o-mini call), `classify_uploaded_file()`
  (adds mismatch detection against `_LABEL_TO_DOCTYPE` map), 14-category `DocType` enum output.
- `table_extractor.py::extract_all_table_rows()` — OCR-first (Tesseract), pdfplumber secondary
  path, GPT-4o-mini row parsing → `TableRow` objects.
- `file_io.py` — see §5.

### `src/models/`
- `submittal.py` — `DocType` (14-value str enum), `SubmittalMetadata` (submittal_id, authority,
  project_name, material_description, created_at — tenant/user fields present only as commented-
  out placeholders), `UploadedFile`, `ClassifiedDocument`.
- `knowledge_store.py` — `DocumentSection` (full text per classified doc), `SubmittalKnowledgeStore`
  (submittal_id, authority, 6 cover-page fields, `sections: list[DocumentSection]`,
  `table_rows: list[dict]`; methods `get_text()`, `has_type()`, `get_present_types()`,
  `get_mismatches()`, `.save()`); module-level `load_store()` with a process-local `_cache` dict;
  persisted at `data/knowledge_stores/{submittal_id}.json`.
- `requirements.py` — `RequirementType` (13-value enum), `VerificationStatus` (5-value enum),
  `VerificationMethod` (6-value enum), `ExpectedValue` (`.is_numeric()`, `.check()` — the
  deterministic pass/fail method), `EvidenceExpectation`, `SpecRequirement`,
  `ReviewRequirementsArtifact`, `EvidenceSnippet`, `RequirementVerification`,
  `RequirementVerificationArtifact` (computed props: `satisfied_count`, `non_compliant_count`,
  `missing_evidence_count`, `mandatory_failures`).
- `findings.py` — `Severity` (PASS/WARNING/CRITICAL), `Finding`, `TableRowFinding`,
  `ReviewReport` (critical_count/warning_count auto-computed via `@model_validator`).
- `query.py` — models for the RAG query pipeline (`StructuredQuery`, etc.).

### `src/config/`
- `paths.py` — `PROJECT_ROOT` (derived from `__file__`, CWD-independent), `DATA_DIR`,
  `CHROMA_PATH`, `STORE_DIR`, `SPECS_DIR`, `SUBMITTALS_DIR`, `ensure_dirs()` (called once in
  `app/main.py`).
- `base_profile.py::AuthorityProfile` (ABC) — `authority`, `index_items`, `required_doc_types`,
  `optional_doc_types`, `requires_avl_check` (default False), `requires_non_toxicity` (default
  False), `extra_requirements`, `get_max_doc_age_days()` (abstract), `is_required()`,
  `chroma_collection_name`, `INDEX_8_VALID_TYPES` frozenset.
- `adm_profile.py::ADMProfile(AuthorityProfile)` — 10-item index, 12 required doc types
  (COVER_PAGE, BOQ, DRAWING, SPECIFICATION_COPY, COMPARISON_TABLE, TECHNICAL_DATASHEET,
  TEST_REPORT, DED_REGISTRATION, MANUFACTURER_GUARANTEE, METHOD_STATEMENT, MAF, MSDF).
  Test reports max age 3×365 days.
- `taqa_profile.py::TAQAProfile(ADMProfile)` — inherits ADM's full index, adds
  `requires_avl_check=True`, `requires_non_toxicity=True`.
- `__init__.py::get_authority_profile(authority)` → looks up `_PROFILES = {"ADM": ADM, "TAQA": TAQA}`
  (module-level singleton instances).

---

## 7. The Streamlit app (`app/`) — REMOVED (2026-07-30)

This section originally documented `app/main.py` + `app/pages/*.py`, the original Streamlit
prototype UI. It has been **deleted from the repo** (`git rm -r app/`) now that `frontend/`
(Next.js) + `apps/api/` (FastAPI) fully replace it — nothing in the active codebase imports
`app.*` anymore (confirmed by grep before deletion). Kept here only as a pointer for anyone
who finds it referenced in old commits or `docs/`: it was the five-page hand-rolled-nav
Streamlit app (`upload` → `review` → `report` / `chat`, plus an admin `spec_manager` page),
superseded feature-for-feature by the production frontend/API.

---

## 8. Scripts (`scripts/`)

- `setup_chromadb.py` — creates the two ChromaDB **Cloud** collections. Targets a path the
  running app does NOT use (app uses local `PersistentClient`). Requires `CHROMA_API_KEY`/
  `CHROMA_TENANT`/`CHROMA_DATABASE` env vars.
- `load_adm_spec.py`, `load_taqa_spec.py` — **1-line stub files, not implemented.** Spec
  indexing is currently only exercised through `app/pages/spec_manager.py` and the
  `experiments/rag/` scripts.
- `verify_langsmith.py` (42 lines) — standalone LangSmith connectivity check.

---

## 9. Data on disk (`data/`)

- `data/knowledge_stores/*.json` — 3 files present: `SCENARIO_01.json`, `SCENARIO_02.json`,
  `TEST_01.json` (leftover artifacts from prior scenario test runs — real
  `SubmittalKnowledgeStore` dumps, useful as fixtures/examples of the JSON shape).
- `data/chromadb/` — not present in the repo listing (git-ignored local SQLite ChromaDB store;
  created on demand by `ensure_dirs()` / first index write).
- No `data/specs/` or `data/submittals/` content currently checked in (also created on demand).

---

## 10. Tests and eval-harness seed material

### `tests/` — **effectively empty.** Every file is a single comment line, zero test functions:
`test_agents.py`, `test_rag.py`, `test_parsers.py`, `test_rules.py`. `conftest.py` only does
`load_dotenv()`. `pytest.ini` defines one marker: `integration` (tests calling real
OpenAI/ChromaDB/Cohere APIs). **No automated unit coverage exists today.**

### `experiments/` — the only real test/eval material in the repo, useful to seed Phase 5's eval harness:
- `experiments/agents/scenario_tests.py` — 5 pytest **integration** tests
  (`test_scenario_01_complete_correct_submittal` … `test_scenario_05_multiple_issues_none_missed`),
  each running the **real** `compile_review_graph()` end-to-end against fixture PDFs in
  `experiments/data/sample_submittals/{folder}/` (not present in this checkout — scenarios
  needing PDF data self-skip via `needs_pdf_data`/`skip_reason` in their fixture JSON) and
  asserting against `experiments/data/expected_findings/scenario_0{1-5}.json` (recommendation,
  critical-count bounds, required/forbidden findings by stage+severity+description substring,
  "all stages completed", "every finding has a source"). Results written to
  `experiments/agents/results/scenario_0{1,2}_result.json` (only 01/02 have been run — both
  present; 03-05 results absent in this checkout).
- Per `docs/claude_handoff.md` §5: scenario 01 (clean submittal) historically **FAILED**
  (expected APPROVE, got CONDITIONAL — clause not indexed in ChromaDB at test time); scenario 02
  (expired DED + missing docs) **PASSED** (RESUBMIT). This is strong evidence that the eval
  harness's real-world accuracy is gated by spec-indexing completeness, not just agent logic.
- `experiments/data/build_golden_dataset.py` — samples ChromaDB chunks via 20 hand-picked
  technical queries across 3 networks (irrigation/road/storm_water), uses GPT-4o to generate
  Q&A pairs → `experiments/data/spec_questions.json` (30 target pairs). Directly reusable for
  a RAG eval harness.
- `experiments/rag/exp01..exp05/` — 5 self-contained RAG experiments (pipeline.py + run.py +
  results.json each) with a `shared/evaluate.py` custom RAGAS-style evaluator (faithfulness,
  answer_relevancy, context_precision, context_recall — sync GPT-4o-mini scorer, RAGAS itself
  was abandoned due to async HTTP failures on macOS). `comparison_results.csv` aggregates all 5.
  exp05 (network metadata filter) is what's in production; results table reproduced in
  `docs/claude_handoff.md` §5.
- `experiments/llm/` — 3 standalone component accuracy tests with saved results:
  `classifier_test.py` (88.5% raw / 100% mismatch-detection / ~96.2% effective accuracy, 26 real
  files), `table_extraction_test.py` (100% row match, 71% field accuracy, "measured" column
  worst at 56% due to OCR noise), `audit_accuracy_test.py` (table auditor error-injection:
  100% detection, but 100% false-positive rate before the deterministic-override fix).

---

## 11. Known incomplete / placeholder areas (confirmed against code, not just docs)

1. **`boq_drawing`, `statement`, `others` graph nodes** — real, wired-in, execute every run,
   return empty finding lists. Zero logic.
2. **`src/rag/submittal_rag/`** — fully implemented, fully unused. Confirmed no caller of
   `embed_submittal_documents()` anywhere outside its own module.
3. **`scripts/load_adm_spec.py` / `load_taqa_spec.py`** — 1-line stubs; spec loading in
   practice happens only via the Streamlit `spec_manager.py` UI or ad hoc `experiments/` scripts.
4. **`scripts/setup_chromadb.py` targets ChromaDB Cloud**, a path the live app has abandoned in
   favor of local `PersistentClient` — this script is stale relative to the current architecture.
5. **`tests/*.py`** — 4 empty placeholder files, no unit coverage.
6. **Section-level metadata filtering** (on top of existing network-level filtering) —
   `section` already exists in ChromaDB chunk metadata but is never used as an active query
   filter; identified in Experiment 5 as the top lever to improve context_precision.
7. **Guarantee-period rule** in `date_checker.py::check_guarantee()` — regex-extracts the
   declared period but the `required_years` comparison parameter is never wired from the spec,
   so only a "no period found" warning path is ever exercised in practice.
8. **Multi-tenancy** — `SubmittalMetadata` has `user_id`/`tenant_id` only as commented-out
   future fields; `submittal_id` is a bare UUID with zero scoping today.

---

## 12. Tech stack confirmed from `requirements.txt`

PyMuPDF, pdfplumber, pytesseract, Pillow (PDF/OCR) · openai, langchain/langchain-openai/
langchain-community, langgraph (`0.1.x`), langsmith (LLM/orchestration/tracing) · chromadb
(`1.x`) (vector DB) · cohere (`5.x`) (reranking) · rank-bm25 (keyword search) · ragas (present
in requirements but abandoned — see engineering log, not actually used) · rapidfuzz (fuzzy
match) · pydantic (`2.x`) · weasyprint + jinja2 (`src/report/` PDF report generation — exists,
not yet covered above: `src/report/generator.py`, `src/report/pdf_report.py`,
`src/report/templates/report.html`) · streamlit (`1.x`) UI · pandas/numpy · pytest/pytest-asyncio.

`.env.example` declares: `OPENAI_API_KEY`, `LANGCHAIN_API_KEY`/`LANGCHAIN_TRACING_V2`/
`LANGCHAIN_PROJECT` (LangSmith), `CHROMA_API_KEY`/`CHROMA_TENANT`/`CHROMA_DATABASE` (Cloud —
unused by the live app, only by the stale `setup_chromadb.py`), `COHERE_API_KEY`. No
Postgres/S3/Cognito vars exist yet — confirms Phase 1 (DB) and Phase 3 (auth) are genuinely
greenfield.

---

## 13. The production layer (Phases 1–3) — built on top of §1–12, `src/` untouched

Everything below is new code in `db/`, `migrations/`, `apps/worker/`, `apps/api/` — none of
it modifies the prototype documented above except the one sanctioned change to
`src/parsers/file_io.py` (§5). Every claim below has been proven against a real running
stack (local Docker Postgres, real S3 bucket, real Cognito User Pool, real OpenAI calls) —
not just written and assumed to work.

### 13.1 Phase 1 — Postgres schema (`db/`, `migrations/`)

- `db/models.py` — SQLAlchemy 2.0 models for all tables.
- `db/session.py` — **three** distinct connection strings, not one, after a real bug was
  found and fixed (see 13.4): `DATABASE_URL` (API, role `api_app`, RLS-constrained),
  `WORKER_DATABASE_URL` (worker, role `worker_app`, `BYPASSRLS`), `ADMIN_DATABASE_URL`
  (migrations only, `postgres` superuser). `get_db(tenant_id)` is the async context manager
  that runs `SET LOCAL app.tenant_id = '<uuid>'` per-request — inlined as a validated literal,
  not a bind parameter, because Postgres's `SET`/`SET LOCAL` doesn't support `$1`-style
  parameters at all (confirmed by a real `PostgresSyntaxError` while testing).
- Migrations, in order:
  - **001** — the full schema from the build plan: `tenants`, `users`, `projects`,
    `project_specs`, `submittals`, `submittal_files`, `submittal_events`, `chat_turns`,
    `jobs`. RLS with `FORCE ROW LEVEL SECURITY` (not just `ENABLE`) on
    `projects`/`submittals`/`chat_turns` — `FORCE` matters because plain `ENABLE` exempts the
    table owner from its own policies.
  - **002** — split `project_specs`' spec metadata into a new global `spec_documents` table
    (no `tenant_id`/`project_id`, deliberately — specs are shared reference data). The
    original 001 design wrongly duplicated `source_s3_key`/`chunk_count` once per project
    selection; caught before any real data existed in the table.
  - **003** — creates the `api_app` and `worker_app` roles (see 13.4) — this migration exists
    *because* of a real security bug, not preemptively.
  - **004** — adds `submittals.knowledge_store_path` (local disk path, not S3 — see the
    migration's own docstring for why `src/models/knowledge_store.py` can't be touched to fix
    this, and why local-disk sharing between API and worker is actually valid for the Phase 6
    target of running both on one EC2 host via docker-compose, not a bug).
- **Verified:** full `upgrade → downgrade → upgrade` round-trips clean on every migration;
  RLS proven to actually block cross-tenant reads under the real `api_app` role (not just the
  superuser, which would silently bypass it) with a real second tenant's data.

### 13.2 Phase 2 — job queue + worker (`apps/worker/`)

- `apps/worker/jobs.py` — `claim_next_job()` uses `FOR UPDATE SKIP LOCKED` (the whole "queue"
  is the `jobs` table). **This is the Phase 1 target for replacement** — see §13.7 — not a
  permanent design; the `jobs` table itself stays as the audit/status record even after SQS
  becomes the actual dispatch mechanism.
- `apps/worker/worker.py` — `run_worker(s3_bucket)` loop; `run_review()` wraps the **real**
  `compile_review_graph()` unchanged; `run_index()` wraps the **real**
  `src/rag/indexing/indexer.py::index_spec_pdf()` unchanged (downloads the spec PDF from S3 to
  a temp file first, since `index_spec_pdf` needs a real filesystem path). `job_type='embed'`
  still has no handler — `src/rag/submittal_rag/` remains fully coded but never called,
  unchanged from §4's finding.
- `src/parsers/file_io.py::load_pdf_bytes()` — **only this function** was changed, made
  dual-mode: local paths read from disk exactly as before (Streamlit unaffected),
  `s3://bucket/key` paths download from S3. `save_upload()` was deliberately left untouched —
  its return value feeds straight into `index_spec_pdf()` in `spec_manager.py`, which needs a
  real local path; swapping it to S3 would have silently broken that flow.
- **Verified twice, both with real submittals from `Test Submittal/`, real ADM specs from
  `specs/`, real OpenAI calls, real S3 files:**
  - Manual seed (`scripts/dev_seed_review.py`) → worker run → `RESUBMIT`, 6 critical /
    16 warnings, correct deterministic numeric overrides, all 11 graph nodes logged in order.
  - Full API-driven run (create project → attach spec → presigned upload → start → worker) →
    `RESUBMIT`, 7 critical / 10 warnings, correct summary. Different submittal, consistent
    correctness both times.
  - All 3 real ADM spec PDFs indexed into local ChromaDB: irrigation (7,886 chunks), road
    (9,631), storm_water (6,318) — 23,835 chunks total, matching the vector store described
    in §4 (still local `PersistentClient`, not Cloud, not Qdrant — Phase 4 unstarted).

### 13.3 Phase 3 — FastAPI + Cognito (`apps/api/`)

- Real Cognito User Pool (`us-east-1`, SPA app client, email-only sign-in, self-registration
  **off** — accounts are admin-provisioned, matching the sales-led B2B model, not open signup).
- `apps/api/auth.py` — verifies JWTs against the pool's live JWKS endpoint (`PyJWKClient`,
  explicit `certifi` SSL context — macOS python.org builds don't reliably find system CA
  roots otherwise, confirmed by a real `CERTIFICATE_VERIFY_FAILED` during testing). Handles
  the real Cognito quirk where ID tokens carry `aud` but access tokens carry `client_id`
  instead, with no `aud` at all.
- `apps/api/dependencies.py` — `get_current_user` is the **only** place `tenant_id` is
  inferred (from the verified token's `sub`, looked up in `users`) rather than trusted from
  a request; every route downstream uses that value, never one from the client.
- Routers, all live and tested: `projects.py` (create/list/detail/attach-spec),
  `submittals.py` (create + presigned upload URLs / start / status / events / list),
  `specs.py` (presigned upload / enqueue index / list, `tenant_admin`-gated writes),
  `chat.py` (ask + history). Upload flow matches the plan exactly: the client PUTs bytes
  straight to S3 via a presigned URL, never through the API process.
- `chat.py` deliberately does **not** depend on `knowledge_store_path` — `query_agent_node`
  only reads `authority`/`submittal_id`/`spec_clause`/`report` from state, all of which live
  in `submittals.report` (JSONB) + a join to `projects.authority`. None of its three routes
  (`spec_rag`, `submittal_rag`, `report_json`) require the knowledge store itself, so chat
  works today without the local-disk-sharing assumption that column exists for.
- **Verified with real tokens, not mocks:** a real Cognito test user created via
  `admin-create-user`, a real access token via `admin-initiate-auth`, exercised against every
  route with a live `uvicorn` server — including uploading 13 real files via real presigned
  URLs and a real chat question that produced a grounded, correctly-routed answer.

### 13.4 A real cross-tenant RLS bypass was found and fixed here — worth understanding

While testing Phase 3 against a real second tenant (not just "does the request succeed"),
the API returned **both** tenants' projects through a real Cognito-authenticated request.
Root cause: the API and worker both connected as the `postgres` superuser (same
`DATABASE_URL` for both) — superusers bypass RLS unconditionally, regardless of `FORCE ROW
LEVEL SECURITY` (`FORCE` only affects the table *owner's* own bypass, never a superuser).
Every RLS policy from migration 001 had been a silent no-op for both processes the entire
time; it only actually got exercised by a one-off `app_user` role created manually during
Phase 1 testing, which was lost the moment that dev docker volume got recreated — which is
exactly why this went undetected until a real second-tenant test against the live API
surfaced it. Fixed via migration 003: `api_app` (regular, RLS-constrained — what the API
actually runs as now) and `worker_app` (`BYPASSRLS`, deliberately — the worker must read a
submittal's own `tenant_id` before it can know what to scope to, a chicken-and-egg problem
plain RLS can't solve for a trusted backend process). Re-tested after the fix with the same
real second tenant: isolation now holds.

### 13.5 Known gaps in the production layer (honest, not hidden)

- **`job_type='embed'`** has no worker handler — same underlying gap as §4/§6
  (`src/rag/submittal_rag/` uncalled), now also visible as an unimplemented job type.
- **Qdrant migration (Phase 4)** hasn't started — `qdrant_collection` column names are
  forward-looking; the actual store is still local ChromaDB.
- **Eval harness (Phase 5)** and **deploy (Phase 6)** haven't started.
- **S3 bucket region is `us-east-1`**, not `me-central-1` (UAE) — a deliberate choice made
  after discussion, not an oversight; worth revisiting before onboarding a real UAE
  government-adjacent client if data residency becomes a contractual requirement.
- **Cognito dev pool has no MFA and uses Cognito's built-in (rate-limited) email sender** —
  fine for a handful of test users, not configured for production send volume or security
  posture yet.
- **`role`-gating exists (`tenant_admin` required for spec writes) but there is no
  `super_admin`-only route yet** for cross-tenant administration (e.g. onboarding a new
  tenant) — that still happens via direct DB access (`scripts/dev_seed_review.py` or manual
  SQL), matching the "admin-provisioned accounts" decision, not yet wrapped in an API.

### 13.6 Phase 0 (production-readiness rebuild) — foundation, done 2026-07-30

`CLAUDE.md`'s original "simplicity for 20-40 clients" framing (no Celery, no SQS, Qdrant
Cloud not self-hosted) was explicitly superseded mid-project: the decision became "invest
properly, one time, so the architecture doesn't need redoing after client #3-5." A new
8-phase production-readiness plan governs everything from here forward (Phase 1 = this
section's SQS/IAM work, Phase 2 = secrets/rate-limiting, Phase 3 = CloudWatch observability,
Phase 4 = RDS + self-hosted Qdrant, Phase 5 = the Layer-2/ports-and-adapters extraction,
Phase 6 = Terraform + systemd deploy, Phase 7 = product-completeness routes from `notes/api.md`).
Phase 0 — the foundation everything else builds on — is complete:

- **`config.py`** — one `pydantic-settings` `Settings` object, validated at boot, replacing
  scattered `os.environ` reads across `apps/api/auth.py`, `apps/api/s3.py`,
  `apps/worker/worker.py`, `db/session.py`. Not yet wired into those call sites — exists and
  is tested, but the scattered reads it's meant to replace are still live (see §13.8 gap).
- **`core/errors.py`** — typed `AppError` hierarchy (`AppError` → `CategoryError` sets
  `component` → `LeafError` sets `error_code` + `retryable`), covering storage/pipeline/
  queue/database/identity/edge. `retryable` is the field Phase 1's SQS worker branches on:
  leave the message for redelivery vs. delete immediately.
- **`core/ports.py` + `adapters/pipeline/`** — the **`ReviewPipelinePort`** seam over frozen
  `src/`. `LangGraphReviewPipeline` wraps `compile_review_graph()`/`stage_files()` completely
  unchanged; `FakeReviewPipeline` is a zero-cost, zero-network double that emits the same 11
  real node names in the same order (including the TAQA `avl_check` / non-TAQA `skip_avl`
  branch) and returns a valid `ReviewResult`. This is the actual unlock — everything
  downstream (tests, eventually the eval harness) runs against the fake, for free, instead of
  paying real OpenAI cost per run.
- **`composition.py`** — the one file allowed to import a concrete adapter;
  `apps/worker/worker.py::run_worker()` now takes an injectable `pipeline: ReviewPipelinePort`
  and defaults to `composition.build_review_pipeline()` at real runtime.
- **`tests/unit/`** — first real test suite (46 tests, `pytest.ini` now actually exercised,
  not just present): typed-error contract tests, `FakeReviewPipeline` node-sequence/branching/
  failure-path tests, composition-root wiring, and two DB-backed tests
  (`test_worker_run_review.py`) that run `run_review()` against a real Postgres transaction
  (self-contained tenant/project/submittal fixture, S3 monkeypatched out) — marked
  `integration` for the DB dependency, but still zero OpenAI cost.
- **`.github/workflows/ci.yml`** — ruff + mypy (scoped to `core/`, `adapters/`,
  `apps/worker/`, `composition.py`, `config.py`, `db/`, `tests/unit/` — deliberately
  excluding frozen `src/` and the (now-deleted) legacy `app/`) + pytest against a real
  Postgres service container, on every push.
- **Verified twice, both against the real dev DB and real S3 files (submittal
  `32b79c90-3d27-4d59-b764-a12d3627ddb3`):** once through `FakeReviewPipeline` (zero cost,
  proves the DB/S3/event-logging wiring), once through the real `LangGraphReviewPipeline`
  (real OpenAI spend, explicit go-ahead given first — proves the port wrapper changed
  nothing: real `RESUBMIT` recommendation, real report + requirements/verification
  artifacts, all 11 real node names logged in order).

### 13.7 Locked decisions for the production-readiness rebuild (recorded so they aren't re-litigated later)

- **Job queue: SQS + a Dead-Letter Queue, not Celery+Redis.** Redis-backed Celery means
  self-operating a broker (HA, backups, patching) for machinery (task routing, canvases,
  result backends) this system doesn't use. SQS is fully managed and gives retry
  (visibility-timeout redelivery) + dead-lettering (`maxReceiveCount`) natively — exactly
  the two things actually needed. The `jobs` table stays as the audit/status record even
  after this lands; SQS becomes the dispatch mechanism, not a replacement for status tracking.
- **Tooling: AWS-native only, no new paid third-party SaaS.** No Sentry (CloudWatch Logs/
  Metrics/Alarms + SNS instead). Self-hosted Qdrant on EC2 (Docker), not Qdrant Cloud —
  reverses the "Qdrant Cloud" line in the old Phase 4 framing below.
- **Worker deployment: still a plain Python process, still on the one EC2 host — but
  supervised via a systemd template unit, not a bare terminal process.** Scaling from N to
  N+1 concurrent reviews means starting one more instance of the same unit (`worker@3`,
  `worker@4`, ...) — all instances long-poll the same SQS queue independently; SQS handles
  not double-delivering a message, no coordination code needed. Concurrency ceiling is the
  one EC2 box's CPU/RAM, not the queue. Considered and explicitly rejected for now: Docker
  (extra moving parts, not needed yet), ECS/Fargate (real infra investment, contradicts
  `CLAUDE.md`'s original "not ECS" framing for pre-PMF), Lambda (15-min timeout too close to
  the ~6-min pipeline with no margin, and packaging LangGraph + PDF/OCR deps for Lambda is
  real friction), Kubernetes (overkill at this scale). Each of these remains a valid future
  upgrade if the system ever outgrows one EC2 host — not a decision being made now.

### 13.8 Known gaps carried forward from Phase 0

- `config.py` is now wired into `apps/worker/worker.py`'s and the API routers' new SQS code
  path (via `composition.build_job_queue()` → `config.get_settings()`), but **not yet** into
  `apps/api/auth.py`, `apps/api/s3.py`, `db/session.py`, or the rest of `apps/worker/worker.py`
  — those still read `os.environ` directly. Folding the rest in is unscheduled busywork, not
  blocking anything.
- `core/ports.py` now has `ReviewPipelinePort` and `JobQueuePort`. Still deliberately absent:
  `SpecIndexPort`, `ObjectStoragePort`, `SubmittalRepository`, `IdentityPort` (all named in
  `notes/architecture.md` §3.3) — added when their own phase needs them (Phase 4, 4, 5, 5 —
  per the kit's volatility rule), not spread speculatively now.
- **IAM hardening (the second half of Phase 1) was explicitly deferred by user decision**:
  the existing personal AWS access key/secret (already used for S3) is reused for SQS rather
  than creating `msr-api-role`/`msr-worker-role` right now. The one thing actually done for
  this: confirmed the key has `AmazonSQSFullAccess` attached. Scoped IAM roles remain a real,
  not-forgotten gap — worth doing before onboarding a real client, just not blocking this
  phase.
- `/health` and `/ready` (the other Phase 1 item from `notes/api.md` §4.1) — not yet built.

### 13.9 Phase 1 (partial) — SQS migration, done 2026-07-30

Real, billed AWS resources created via the Console (not Terraform/CLI — deliberate, matches
"foundation first, minimal ceremony" for this pass): queue `msr-review-jobs` + DLQ
`msr-review-jobs-dlq`, both `us-east-1`, 20-minute visibility timeout (comfortably covers the
~6-minute real pipeline), 20s long-poll wait time, `maxReceiveCount=3` redrive to the DLQ.

- **`core/ports.py::JobQueuePort`** — minimal by design: `send`/`receive`/`delete` only, no
  `extend_visibility`/heartbeat (the 20-min timeout has enough margin that it isn't needed
  yet). Message body is just the job id — the `jobs` Postgres table stays the single source
  of truth for `job_type`/`submittal_id`/`payload`; SQS is purely dispatch+retry layered on
  top, not a second copy of the data.
- **`adapters/queue/sqs_queue.py`** (`SQSJobQueue`) — the real adapter, botocore exceptions
  translated to `core.errors.QueueTransientError`. **`adapters/queue/fake_queue.py`**
  (`FakeJobQueue`) — in-memory double mirroring real SQS's essential behavior (a received
  message is invisible until acked or requeued), used by every test so none of them touch AWS.
- **`apps/worker/jobs.py`** — `claim_next_job` (the old `FOR UPDATE SKIP LOCKED` claim)
  replaced by `try_claim_job` (atomic PENDING→RUNNING flip by job id, returns `None` if
  already claimed — handles SQS's at-least-once delivery safely) and `requeue_job` (reverts
  RUNNING→PENDING so a redelivered message can re-claim after a retryable failure).
- **`apps/worker/worker.py`** — main loop rewritten: `queue.receive()` (long-polls 20s, no
  more manual `time.sleep` between empty polls) → `try_claim_job` → `_handle_job` → on
  success, `mark_done` + `queue.delete()` (ack); on a **retryable** typed error, `requeue_job`
  + leave the message un-acked (SQS redelivers after the visibility timeout expires — real
  retry, not simulated); on a **non-retryable** typed error (or any untyped/unrecognized
  exception — deliberately treated as non-retryable by default, same philosophy as
  `adapters/pipeline/langgraph_pipeline.py`'s catch-all), `mark_failed` + `queue.delete()` so
  it never wastes a redelivery. The per-message logic was pulled out into
  `_process_message()` specifically so it's testable without needing to break out of an
  infinite loop.
- **`apps/api/routers/submittals.py::/start`** and **`specs.py::/index`** — both now call
  `queue.send(job_id)` right after the `jobs` row INSERT commits (never before — a worker
  must never be able to receive a job id the DB hasn't durably recorded yet). The `SQSJobQueue`
  instance is built once at module import (matches `apps/api/s3.py`'s existing boto3-client
  pattern), and `send()` runs via `asyncio.to_thread` since boto3 is synchronous and would
  otherwise block the event loop.
- **17 new tests** (`tests/unit/test_fake_queue.py`, `test_sqs_queue.py`,
  `test_worker_sqs_loop.py`) — `FakeJobQueue` behavior, `SQSJobQueue` against a mocked
  boto3 client (`unittest.mock`, not moto — avoided a new test dependency), and all 4 real
  scenarios for `_process_message` against real Postgres: success, retryable-failure-requeues,
  non-retryable-failure-fails-and-acks, unknown-job-id-acks-without-touching-DB. 63 tests
  total now, all zero-cost.
- Added `pyproject.toml` with one `[tool.ruff.lint.flake8-bugbear]` entry
  (`extend-immutable-calls` for `fastapi.Depends`/`Query`/`Body`/`Path`) — without it, ruff's
  B008 rule flags every FastAPI route's dependency-injection pattern as a bug, which it isn't.
- **Verified against the real, live SQS queue** (not just `FakeJobQueue`): sent a real
  message, received it via a real 20s long-poll, processed it through the real
  `_process_message` (with `FakeReviewPipeline` — zero OpenAI cost, this test isolates the
  SQS wiring specifically), confirmed the DB updated correctly (job `DONE`, submittal
  `COMPLETED`), and confirmed the message was actually acked — a second real receive came
  back empty.
- **Not done in this pass** (see §13.8): scoped IAM roles, `/health`/`/ready`, and actually
  running the worker as a systemd unit (still a bare `python -m apps.worker.worker` process
  today — the SQS dispatch mechanism is real, but the "supervised, restartable, easy to
  scale to N copies" deployment piece from the locked decisions in §13.7 hasn't been built
  yet).

### 13.10 Frontend — full desktop redesign, done 2026-07-31

The Next.js frontend (previously a functional-but-generic Tailwind UI) was rebuilt against a
real design handoff (`frontend/design_handoff_clause_qc_review/README.md` +
`frontend-api-reference.md`, itself derived from `notes/frontend.md`). The existing working
logic (retry-safe upload, SSE progress streaming, direct-to-S3 upload) was kept and re-skinned,
not rewritten from scratch.

- **Design system**: Instrument Sans + IBM Plex Mono fonts, full color/spacing/radius token
  set as Tailwind theme variables, `lucide-react` icons, a toast system, shared
  `Button`/`Chip`/`StatusChip` primitives, `lib/status.ts` mapping real API enum values
  (`satisfied`/`non_compliant`/etc.) to the design's MET/PARTIAL/NOT MET language.
- **Every screen rebuilt**: Login, Projects home, Create Project (now a real modal, not a
  route), Project Register (stats computed from real submittal data, not invented), Submit
  (real XHR upload progress, no fake cover-page auto-fill — that's not a real API feature),
  Progress (the real 11-stage weighted checklist, actual node names/weights), Findings
  Report (verdict banner, compliance matrix from real `citations` + `table_audit_findings`,
  local-only confirm/dismiss per G1, chat rail), and a new Spec Library admin screen
  (role-gated on `tenant_admin`).
- **Also added**: session-expired redirect with return-path + inline note, the "no tenant"
  404 edge state (`GET /me` returning 404).
- **Verified**: `next build`, `tsc --noEmit`, and `eslint` all pass clean.
- **Known gap, explicit**: **desktop/laptop only.** The sidebar hides below `md` breakpoint
  so mobile doesn't visually break, but there is no mobile-specific layout yet — no bottom
  tab bar, tables don't collapse to cards, etc. Same codebase, same components either way
  (Tailwind responsive classes, not a separate app) — just not built yet.

### 13.11 Qdrant migration (spec library) — done 2026-07-31

Per the locked decision in §13.7 (self-hosted Qdrant, not Qdrant Cloud). Scoped narrowly to
the spec library only — submittal-level RAG (see below) was deliberately deferred as a
separate, later step.

- **Real blocker found and explicitly resolved**: ChromaDB was hardwired directly into 5
  frozen `src/` files (`src/rag/indexing/indexer.py`, `src/rag/query/hybrid_retriever.py`,
  `src/rag/query/parent_fetcher.py`, plus `src/rag/submittal_rag/store.py` and `retriever.py`
  for the deferred submittal-RAG piece) — not a drop-in swap underneath them. Per CLAUDE.md
  rule 1's "stop and ask first," explicit sign-off was obtained to extend the same narrow,
  mechanical exception already made for `file_io.py` to these files: swap only the
  `chromadb.PersistentClient` calls for a Qdrant client, preserve all business logic
  (chunking, query construction, RRF fusion, reranking) unchanged.
- **Collection structure**: one collection per authority (`adm_specifications`,
  `taqa_specifications`) — unchanged from the ChromaDB design, still shared/global across
  all tenants, correctly so (spec text is public reference data, not tenant data).
- **One real semantic gap handled deliberately**: ChromaDB's `where_document={"$contains":
  ...}` substring search (used in `parent_fetcher.py`'s `get_parent_ids_for_chunks`) has no
  Qdrant-native equivalent (Qdrant's payload text match is token-based, not substring).
  Preserved identical behavior via a cached full-collection scroll + Python substring match,
  rather than approximating with different-semantics token matching.
- **Self-hosted via Docker**: `qdrant` service added to `docker-compose.yml` (same pattern as
  the existing local Postgres), matching the target of the same image running on EC2 in prod
  later.
- **Re-indexed all 3 real ADM spec PDFs** — 23,835 chunks total, **exactly matching** the
  original ChromaDB counts (irrigation 7,886 + road 9,631 + storm_water 6,318).
- **Verified against a real, paid review run** (not just a smoke test): real spec clauses
  cited (10.2.2, 11.2.2, Table 10.2-1), real deterministic numeric checks, real extracted
  citation text, a sensible `RESUBMIT` recommendation consistent with this same submittal's
  earlier ChromaDB-backed run — no retrieval quality regression.
- **Deliberately deferred**: submittal-level RAG (activating the currently-dead
  `src/rag/submittal_rag/` code so a submittal's own uploaded documents get embedded and are
  queryable in chat) — a genuinely new feature, not an engine swap, planned as a separate
  step. Also deferred: custom/private per-project specs (discussed and explicitly shelved —
  every spec today is global/admin-ingested, visible to all tenants, matching current
  behavior exactly).

### 13.12 LangSmith tracing (notes/11_pilot_bar_tickets.md Ticket 0) — done 2026-07-31

- **`apps/api/main.py`** — added `load_dotenv()` before the router imports. Real gap found:
  `apps/api/s3.py`, `apps/api/auth.py`, and `db/session.py` all read required vars via bare
  `os.environ[...]` at import time, and nothing loaded `.env` for the API process (unlike
  the worker, which already did in its `__main__`). Without this, LangSmith tracing for the
  chat route (`query_agent_node`, called directly from `apps/api/routers/chat.py`, already
  `@traceable`-instrumented inside frozen `src/agents/query_agent.py`) only fired if the
  shell happened to have `.env` pre-exported.
- **`adapters/pipeline/langgraph_pipeline.py`** — `graph.stream()` now passes a `config` with
  `tags=["review", authority]`, `metadata={tenant_id, project_id, submittal_id}`, and
  `run_name=f"review:{submittal_id}"`. This is the seam file (not frozen `src/`), so the
  whole 11-node graph shows up as one LangSmith trace, searchable by tenant/project/submittal.
- **`tests/unit/conftest.py`** (new) — autouse fixture forcing `LANGCHAIN_TRACING_V2=false`
  and clearing `LANGCHAIN_API_KEY` for the whole unit suite, so a developer's own shell
  exports can't leak tracing into test runs.
- **`tests/unit/test_langgraph_pipeline.py`** (new, 4 tests) — against a fake compiled graph
  (no OpenAI/AWS): confirms the correct tags/metadata/run_name reach `.stream()`, existing
  node-callback and report-extraction behavior is unchanged, and the typed-error path still
  fires when no report is produced.
- **Verified against a real, paid review run** (submittal `32b79c90-3d27-4d59-b764-a12d3627ddb3`,
  job `f895ebd0-58f5-445f-b28f-4fbea255bc76`, ~9.5 min, real `RESUBMIT` recommendation) — not
  just the code path. Confirmed directly in the LangSmith UI: a single trace named
  `review:32b79c90-3d27-4d59-b764-a12d3627ddb3` containing all 11 real nodes nested
  underneath (including the correct `skip_avl` branch for this ADM submittal), with
  `Attributes → Tags` showing `["ADM", "review"]` and `Attributes → Metadata` showing the
  correct `tenant_id`/`project_id`/`submittal_id`.
- **Two adjacent, pre-existing bugs found while running the verification, not fixed (out of
  this ticket's scope)**:
  1. `apps/worker/worker.py::run_review()`'s `submittal_events` sequence counter always
     starts at `seq = 0` per call instead of continuing from the max existing sequence
     number for that submittal — re-running a review against a submittal ID that already has
     events (e.g. `scripts/dev_seed_review.py`'s fixed dev submittal ID) hits
     `uq_submittal_events_seq` and fails the whole job with a non-retryable
     `UniqueViolation`. Worked around during verification by manually deleting the stale
     `submittal_events` rows before re-running; needs a real fix (query current max seq, or
     scope the counter differently) before this can be a repeatable dev/QA flow.
  2. `submittals.error_message` isn't cleared on a later successful run — after the above
     failure was fixed and the submittal re-run to completion, `status` correctly shows
     `COMPLETED` but `error_message` still holds the stale text from the earlier failed
     attempt.
  3. `scripts/dev_seed_review.py` predates the Phase 1 SQS migration (§13.9) — it inserts a
     `jobs` row but never calls `queue.send()`, and the worker no longer polls Postgres
     directly. Running the script alone no longer triggers a worker pickup; the job id must
     be manually sent to SQS afterward (`build_job_queue().send(job_id)`). The script's
     docstring/instructions are now stale relative to how the worker actually dispatches.

### 13.13 Persist findings with stable IDs (notes/11_pilot_bar_tickets.md Ticket 1) — done 2026-08-05

Full detail, design rationale, and testing steps in `notes/tickets/ticket1.md`. Summary:

- New `findings` table (migration `006_findings.py`, `db/models.py::Finding`), RLS-enabled
  (`ENABLE`/`FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy, identical pattern to
  migration 001). Rows are immutable — nothing ever `UPDATE`s a finding after insert; human
  decisions are Ticket 2's separate append-only table.
- **`src/` untouched.** `apps/worker/findings.py::extract_findings()` (new, pure, zero DB/
  network) flattens the pipeline's already-computed `report` dict into findings rows —
  it never imports from `src/agents/` or `src/models/`.
- `apps/worker/worker.py::run_review()` inserts findings in the **same transaction** as the
  completion `UPDATE` — both succeed or both roll back together.
- New `GET /api/v1/submittals/{submittal_id}/findings`
  (`apps/api/routers/submittals.py`), RLS-scoped via the existing `get_db(tenant_id)`
  pattern. Not yet called by the frontend — that's Ticket 3's job (confirm/dismiss/edit
  needs a `finding_id` to act against).
- **Five columns are real NULL for every finding in this ticket, by design, not a gap
  discovered later**: `clause_reference`, `spec_document_id`, `spec_page`,
  `source_document_id`, `source_page`. The frozen `Finding`/`TableRowFinding` models only
  carry per-finding citation data for the `spec_verification` category (and even there,
  it's in a separate `verification_artifact` JSONB keyed by `requirement_id`, not
  `finding_id`, with no clean 1:1 join). Real per-finding citations across all 9 categories
  need the underlying agents themselves to produce and attach that provenance — that's
  **Ticket 11**'s job, and will very likely need its own narrow, explicitly-authorized
  exception to touch specific `src/agents/` files (same pattern as the Qdrant migration,
  §13.11, and Ticket 5's three placeholder nodes). `confidence`/`model_version`/
  `prompt_version` are NULL too — `confidence` is explicitly Ticket 8's job; no ticket yet
  owns real per-model/per-prompt versioning (only the coarser `pipeline_version` exists).
- **Backfill run for real against this dev DB**, not just designed: `scripts/
  backfill_findings.py` (new) derives findings from the `submittals.report` JSONB every
  `COMPLETED` submittal already has — no re-running real reviews needed. Result: **9 real
  submittals backfilled, 298 real findings**. Confirmed idempotent — re-running the script
  inserted 0 new rows.
- **Verified against real data, not just unit tests**: migration round-tripped
  (`upgrade → downgrade → upgrade`) clean; the real backfilled findings spot-checked
  correct (categories, severities including the `pass → observation` mapping, real
  descriptions); the new endpoint's exact query/RLS path executed directly against the
  real dev DB via `db.session.get_db(tenant_id=...)` and returned correct tenant-scoped data.
- 20 new/extended tests (120 total passing, ruff/mypy clean on every CI-linted path):
  pure `extract_findings()` tests, an extended `test_worker_run_review.py` proving stable
  IDs survive a simulated restart (new DB session, re-read, identical IDs — the literal
  acceptance criterion), and a real cross-tenant RLS test
  (`tests/unit/test_findings_rls.py`) proving tenant A cannot read tenant B's findings even
  with no `WHERE tenant_id` clause, nor fetch it by exact primary key.
- **Known gap, explicit**: no FastAPI `TestClient` test for the new endpoint — no existing
  test in this codebase exercises a route end-to-end yet (auth needs a real Cognito JWT, no
  mock-auth fixture exists). Verified the underlying query/RLS path directly instead.

### 13.14 Decision event log + reason codes (notes/11_pilot_bar_tickets.md Ticket 2) — done 2026-08-05

Full detail, design rationale, and testing steps in `notes/tickets/ticket2.md`. Summary:

- Two new tables (migration `007_finding_decisions.py`):
  - `reason_codes` — global, shared taxonomy (no `tenant_id`/`project_id`, same rationale
    as `spec_documents`), seeded in the migration with the 10-code starting set from
    notes/10_stage1_product_and_data_spec.md §A3 (5 dismiss, 3 edit, 2 confirm). No RLS,
    matching `spec_documents`' precedent.
  - `finding_decisions` — append-only, RLS-enabled (same `tenant_isolation` pattern as
    `findings`/migration 001). One row per human action (`confirm`/`dismiss`/`edit`) on a
    finding (migration 006, Ticket 1); nothing ever `UPDATE`s or deletes a row.
  - **Composite FK `(reason_code, action) → reason_codes(code, action)`** — a real
    DB-level guarantee, not just an API-layer check, that a `dismiss`-only reason code
    can't be attached to a `confirm`/`edit` decision. Proven with a real `IntegrityError`
    test, not just written and assumed to hold.
  - `CHECK (action = 'edit' OR corrected_fields IS NULL)` — non-edit decisions can't carry
    a correction diff.
- New `apps/api/routers/findings.py`: `POST /api/v1/findings/{finding_id}/decisions`,
  `GET /api/v1/findings/{finding_id}/decisions` (full ordered history — added beyond the
  ticket's literal API list because the acceptance criterion explicitly requires "the full
  history is queryable"), `GET /api/v1/reason-codes?action=...`.
- `GET /api/v1/submittals/{submittal_id}/findings` (Ticket 1's endpoint,
  `apps/api/routers/submittals.py`) now `LEFT JOIN LATERAL`s each finding's *latest*
  decision (`ORDER BY created_at DESC LIMIT 1`) as `current_decision` — computed at read
  time on every request, never stored as a mutable status column anywhere, per the
  ticket's explicit instruction.
- **Verified against real data, not just unit tests**: recorded a real `confirm` then a
  real `edit` decision against one of Ticket 1's real backfilled findings
  (`1a1bef2d-e4e4-435a-a841-5bf6e2d6c879`), ran the exact joined query the route uses,
  confirmed `edit` correctly won as `current_decision` while both rows remained queryable
  in the full history — then cleaned up the scratch rows afterward.
- 7 new tests (127 total passing, ruff/mypy clean including the new router and
  `apps/api/main.py`, which — like Ticket 0's finding — aren't in CI's current lint scope;
  pre-existing gap, not introduced here, flagged not fixed): decision recording,
  latest-wins-while-history-survives, `reason_code` required, `reason_code` must match the
  decision's `action` (real DB constraint violation), `corrected_fields` rejected for
  non-edit actions, and real cross-tenant RLS isolation on `finding_decisions`.
- **Known gap, explicit**: same as Ticket 1 — no FastAPI `TestClient` test for the new
  routes; verified the underlying query/RLS path directly instead, consistent with every
  other route in this codebase (none are tested end-to-end over HTTP yet).

### 13.15 Wire Confirm/Dismiss/Edit in the UI (notes/11_pilot_bar_tickets.md Ticket 3) — done 2026-08-05

Full detail, design rationale, and testing steps in `notes/tickets/ticket3.md`. Frontend-only
— no backend changes; everything it needed already existed from Tickets 1–2. Summary:

- `frontend/src/components/report-view.tsx`'s findings report now records real decisions
  against the persisted `findings`/`finding_decisions` tables instead of local-only state
  that reset on refresh. Confirm/Dismiss/Edit all open a reason picker (`GET /reason-codes`,
  human-readable labels, never raw codes) with an optional note; Edit additionally lets the
  reviewer change severity/clause_reference/description, showing the untouched original
  ("AI ORIGINAL") beneath. Optimistic updates with rollback-and-toast on failure — the UI
  never shows a decision that didn't actually save.
- **The hard part**: the rich report/citations JSON (no stable id) and the persisted
  `findings` table (stable id, but 5 columns intentionally NULL per Ticket 1) had to be
  overlaid, not swapped — a new `buildKeyToFindingId()` matching layer attaches a real
  finding id to each existing row via positional correlation (8 of 9 categories) or the
  embedded `[REQ-xxx]` requirement id (`spec_verification`, matched against `citations[]`).
  This is inherently fragile (no shared join key exists) and is a direct, foreseeable
  consequence of Ticket 1's honest NULL-citation-columns decision — real evidence for why
  Ticket 11 (citations attached at the source, in `src/agents/`) matters, not just a nicer
  UI. Degrades gracefully (a row that can't be matched shows "Decision not available" rather
  than crashing) but that path wasn't exercised against real mismatched data during testing.
- Keyboard shortcuts (`c`/`d` on an expanded row) **open** the reason picker rather than
  blind-submitting — a reason is required at the DB level for every action including edit
  (confirmed correct behavior during testing, not a bug), so a one-keystroke auto-submit
  would have meant silently picking an arbitrary reason code.
- **Real correction made mid-session, worth remembering**: the first implementation synced
  the whole `findings` prop into local state via a plain `useEffect`, which this repo's
  `eslint-config-next` flagged as `react-hooks/set-state-in-effect` (a rule not in most
  training data — cascading-render risk). Fixed by keeping only a small
  `{findingId: decision}` overrides map in state, merged with the `findings` prop via
  `useMemo` at render time instead. Documented in `frontend/AGENTS.md`'s new "Lessons from
  real sessions" section (added below the pre-existing, seemingly tool-managed
  `<!-- BEGIN/END:nextjs-agent-rules -->` block, left untouched), along with a note that no
  frontend test framework exists in this repo (`tsc`/`eslint`/`build` + manual browser
  testing is the only verification available) and that no backend route-testing harness
  exists either (new frontend API calls must be cross-checked against real router source,
  not just the hand-written TypeScript types in `lib/api.ts`, which can drift).
- **Verified**: `tsc --noEmit`/`eslint`/`next build` all clean, plus real manual testing
  against a real running stack (Postgres/Qdrant/uvicorn/`next dev`) and one of Ticket 1's
  real backfilled submittals (`32b79c90-3d27-4d59-b764-a12d3627ddb3`) — confirmed directly
  by the user: confirm/dismiss/edit all persist correctly through a hard refresh (the
  literal acceptance criterion), rollback-on-failure works, keyboard shortcuts work and
  don't hijack normal typing, and reason-required-on-edit behaves as designed.
- **Known gaps, explicit**: no automated frontend tests (no framework exists, none added
  without asking first); actor display is "you"/"another reviewer" only — no user-directory
  endpoint exists to show a real name; no frontend UI yet for
  `GET /findings/{id}/decisions`'s full history (only the current decision is shown);
  `corrected_fields` still has no server-side key-allowlist validation (Ticket 2's gap,
  now known to only ever receive `severity`/`clause_reference`/`description` in practice).
- **Post-session fix, worth recording**: `buildKeyToFindingId()`'s generic-category
  positional matching had a real bug at ship time — it only advanced its per-category
  finding-id queue for items that passed the `.filter(f => f.description)` check, while
  `apps/worker/findings.py::extract_findings()` inserts one row per item *unconditionally*.
  The two queues drift out of alignment the moment a single empty-description item appears
  in a category, silently misattributing every later item in that category to the wrong
  finding id — worse than the intended "unavailable" degradation, not just an edge case of
  it. Fixed by always shifting the queue per source item and only skipping the *map write*
  for filtered-out items, so queue and item positions stay 1:1 regardless of filtering.

### 13.16 Report ordering: group by category (notes/11_pilot_bar_tickets.md Ticket 17) — done 2026-08-05

Full detail in `notes/tickets/ticket17.md`. Frontend-only, small. Summary:

- `frontend/src/components/report-view.tsx`'s findings matrix previously sorted purely by
  severity across all 9 categories — a critical `table_audit` row and a critical
  `spec_verification` row could land adjacent while two same-category findings ended up
  pages apart. Real feedback from Arsalan (`notes/MeetingBhaiya .md`, 2026-07-31 meeting):
  group by category first, severity only within each group.
- **The one open design question the ticket itself flagged** (fixed pipeline order vs. the
  order documents were actually uploaded in) was put to the user rather than guessed —
  answered: **fixed pipeline order**. Upload order has no well-defined meaning today
  (bundled PDFs, a single category spanning multiple documents), so this reuses the exact
  same order `src/models/findings.py::ReviewReport`'s fields and
  `apps/worker/findings.py::_STANDARD_REPORT_KEYS` already use elsewhere in this codebase —
  not a new invented ordering.
- Two-key sort: `CATEGORY_ORDER[a.category] - CATEGORY_ORDER[b.category] ||
  SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]`. The old single-key sort's
  rationale comment (quoting the original design README: "don't show a flat undifferentiated
  list") was replaced, not left stale, since it now describes the opposite of what the code
  does.
- **Verified**: `tsc --noEmit`/`eslint`/`next build` all clean. Not re-verified against a
  real running stack in-browser this session (no functional/data-shape risk — pure
  client-side sort comparator over data already fetched and rendering correctly per Ticket
  3's verification) — worth a quick visual confirmation next time the report page is open
  for another reason, not treated as a blocking gap.

---

## What's next

**Done:** Phase 0 (§13.6-13.8) — foundation. Phase 1 **partial** (§13.9) — SQS migration
real and verified; IAM roles, `/health`/`/ready`, and systemd deployment still pending, all
by explicit deferral, not oversight. Frontend desktop redesign (§13.10) — complete. Qdrant
spec-library migration (§13.11) — complete and verified. LangSmith tracing (§13.12,
notes/11_pilot_bar_tickets.md Ticket 0) — complete and verified against a real traced
review. Findings persisted with stable IDs (§13.13, Ticket 1) — complete and verified
against real backfilled data. Decision event log + reason codes (§13.14, Ticket 2) —
complete and verified against real decisions on real findings. Confirm/Dismiss/Edit wired
into the UI (§13.15, Ticket 3) — complete and verified by the user against a real running
stack, closing out the entire "persisted findings + human decisions" arc (Tickets 1–3).
Report findings grouped by category (§13.16, Ticket 17) — complete.

**Note**: `notes/11_pilot_bar_tickets.md` grew Tickets 13–19 on 2026-07-31, from a real
requirements meeting with Arsalan (`notes/MeetingBhaiya .md`) — not internal engineering
cleanup. Tickets 14 and 18 each contain their own open design question flagged inline,
same as Ticket 17 did; resolve those with the user before implementing, don't guess.

**Pending, in no particular committed order:**

1. **Phase 1 leftovers** — scoped IAM roles (`msr-api-role`/`msr-worker-role`, currently
   using personal AWS keys), `/health`/`/ready` endpoints, worker running as a supervised
   systemd unit (still a bare process).
2. **Phase 2** — AWS Parameter Store for secrets, SES email, per-tenant rate limiting.
3. **Phase 3** — CloudWatch logs/metrics + SNS alarms.
4. **RDS** — explicitly staying on Docker Postgres for now (cost decision); migrating later
   via `pg_dump`/`pg_restore` or AWS DMS is straightforward whenever needed.
5. **Submittal-level RAG** — activate the dead `src/rag/submittal_rag/` code (chat grounded
   in a submittal's own uploaded documents, not just the spec) — deferred, see §13.11.
6. **Phase 5** — the Layer-2 extraction (repositories, policies, pipelines) absorbing the
   ~28 raw SQL statements currently inline in routers/worker.
7. **Phase 6** — actual deployment (Terraform, EC2, ALB, HTTPS) — nothing is on AWS yet
   beyond S3/Cognito/SQS; everything else still runs locally.
8. **Phase 7 / product-completeness routes** — see `notes/api.md` §3-4 for the full gap
   list: PDF export, cancel/retry, tenant/user admin screens. The persisted-findings +
   human-decisions arc (Tickets 1–3, §13.13-13.15) is now fully done, backend and frontend.
   Remaining in that area: a user-directory lookup so decision captions can show a real
   name instead of "you"/"another reviewer" (no ticket owns this yet), and a frontend UI
   for the full decision history (`GET /findings/{id}/decisions` has no consumer yet).
9. **Mobile responsiveness** — the frontend (§13.10) is desktop/laptop only right now.
10. **Custom/private per-project specs** — discussed and explicitly shelved; every spec
    today is global, admin-ingested, visible to all tenants.

---

## Superseded: original Phase 4-6 framing (kept for history, see §13.7 for what's current)

Phases 1–3 are built and verified against real infrastructure, including one real security
bug (13.4) found and fixed by testing with a real second tenant rather than trusting the
design on paper. Remaining per `planning/05_build_plan_for_claude_code.md`: Phase 4 (Qdrant
migration), Phase 5 (eval harness, parallel track), Phase 6 (deploy — one EC2 running API +
worker together, one RDS, the S3 bucket(s) already created, Qdrant Cloud, this same Cognito
pool promoted or a fresh prod pool created alongside the prod S3 bucket).
