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

## 7. The Streamlit app (`app/`)

- `app/main.py` — entry point. Inserts project root into `sys.path` (Streamlit quirk), loads
  `.env`, calls `ensure_dirs()`, defines `st.session_state` defaults (`page`, `authority`,
  `metadata`, `review_complete`, `knowledge_store_id`, `report`, `conversation_history`), and a
  hand-rolled sidebar nav (`_NAV` list) that gates `review`/`report`/`chat` pages behind
  `review_complete`. NOT Streamlit's native multi-page routing — deliberate choice (see
  handoff Issue 5.5) to keep gating logic centralized.
- `app/pages/upload.py` (112 lines) — authority selector, project name, file uploader (per-index
  or single bundled PDF), builds `SubmittalMetadata`, calls `save_upload()` then `stage_files()`,
  triggers navigation to `review`.
- `app/pages/review.py` (157 lines) — calls `compile_review_graph()`, drives `graph.stream()`,
  renders live per-node progress.
- `app/pages/report.py` (231 lines) — renders every stage's findings as color-coded expandable
  sections (red/yellow/green), a dedicated comparison-table view, plain-text export.
- `app/pages/chat.py` (125 lines) — post-review Q&A UI calling `handle_query()`.
- `app/pages/spec_manager.py` (169 lines) — admin UI to upload+index authority spec PDFs
  (Pipeline 1 trigger).

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
  is the `jobs` table — no Celery, no SQS, per `CLAUDE.md` rule 3).
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

---

## What's next

Phases 1–3 are built and verified against real infrastructure, including one real security
bug (13.4) found and fixed by testing with a real second tenant rather than trusting the
design on paper. Remaining per `planning/05_build_plan_for_claude_code.md`: Phase 4 (Qdrant
migration), Phase 5 (eval harness, parallel track), Phase 6 (deploy — one EC2 running API +
worker together, one RDS, the S3 bucket(s) already created, Qdrant Cloud, this same Cognito
pool promoted or a fresh prod pool created alongside the prod S3 bucket).
