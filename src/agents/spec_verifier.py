from __future__ import annotations

import json

from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.knowledge_store import SubmittalKnowledgeStore, load_store
from src.models.requirements import (
    EvidenceExpectation,
    EvidenceSnippet,
    ExpectedValue,
    RequirementType,
    RequirementVerification,
    RequirementVerificationArtifact,
    ReviewRequirementsArtifact,
    SpecRequirement,
    VerificationMethod,
    VerificationStatus,
)
from src.models.submittal import DocType
from src.rag.query.context_assembler import (
    EMPTY_CONTEXT_SENTINEL,
    assemble_spec_context,
    assemble_spec_context_enriched,
)

_MODEL = "gpt-4o-mini"
_MAX_DOC_CHARS = 8000   # per document type — enough to cover several pages of a test report

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = wrap_openai(OpenAI())
    return _client


# ── Prompts ────────────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """You extract verifiable engineering requirements from construction specification text.

For each distinct, checkable requirement return:
- id: sequential "R-001", "R-002", ...
- requirement_type: classify using the rules below
- normalized_requirement: concise statement e.g. "Minimum wall thickness ≥ 6 mm"
- expected_value:
    text: raw value string or null
    operator: ">=" | "<=" | "==" | "in_range" | null
    numeric_min: number or null  (lower bound, or single value)
    numeric_max: number or null  (upper bound, only for in_range)
    unit: string or null
- evidence_expectation: infer from requirement_type and wording (rules below)
    required_sources: list — ALL must provide evidence
    optional_sources: list — at least minimum_optional_matches must provide evidence
    minimum_optional_matches: integer
- source_clause: subsection number visible in text e.g. "26.3.2", else ""
- source_page: integer if visible, else 0
- source_text: exact sentence(s) from spec that state this requirement
- mandatory: true unless requirement is clearly conditional or informational
- comparison_table_required: true or false (rules below)

━━━ REQUIREMENT TYPE RULES ━━━
dimension    — physical measurement: thickness, width, diameter, weight per area
standard     — compliance with a code: "comply with BS EN 805"
material     — material type/grade/composition: "HDPE PE100", "Grade 60"
test         — requires a lab test result: "tested to ASTM C494", "test report shall show"
certificate  — requires a third-party certificate: "NSF 61 certified", "CE marked"
approval     — requires prior authority approval: "previously approved by ADM"
performance  — performance value without a mandatory independent test:
               "tensile strength ≥ 500 N", "softening point", "elongation at break"
installation — how or where to physically install: "laid 300 mm above pipeline",
               "backfill with sand", "overlap of 150 mm"
experience   — track record or proven use: "5 years in UAE", "proven track record"
administrative — documentation submission requirements: "contractor shall submit",
                 "provide product data sheet"
warranty     — guarantee period: "10-year warranty"
procedural   — process requirement: "method statement required", "ITP required"
other        — anything that does not fit above

━━━ OPERATOR INFERENCE RULES (critical — do not default to ==) ━━━
"minimum", "not less than", "≥", "at least", "shall not be less than"  → operator: ">="
"maximum", "not more than", "≤", "shall not exceed", "up to"           → operator: "<="
"between X and Y", "X to Y", "from X to Y", "range X–Y"               → operator: "in_range"
"exactly", "shall be exactly"                                           → operator: "=="
Plain performance value with no qualifier (e.g. "Tensile Strength: 500 N"):
  → default to ">=" — engineering specs state minimums, not exact values, for performance.
  NEVER use "==" unless the spec explicitly requires an exact value.
  A product exceeding a minimum is always acceptable unless an upper limit is stated.

━━━ EVIDENCE SOURCE RULES (infer from engineering meaning, not just wording) ━━━

KEY PRINCIPLE: For most product properties, EITHER the technical datasheet OR the test report
can serve as evidence. Do NOT set only one source as required unless the spec explicitly
mandates it. The verifier will search ALL listed sources — set both when either can satisfy.

dimension (thickness, width, diameter):
  required_sources: []
  optional_sources: ["technical_datasheet", "test_report"]
  minimum_optional_matches: 1
  ← Either manufacturer declaration OR independent test result is sufficient

performance (tensile strength, elongation, tear resistance, softening point, permeability):
  required_sources: []
  optional_sources: ["technical_datasheet", "test_report"]
  minimum_optional_matches: 1
  ← Test report is stronger evidence but datasheet is also acceptable

material (material type/grade/composition):
  required_sources: ["technical_datasheet"]
  optional_sources: []
  ← Material composition is a manufacturer-declared property; test report rarely states it

test ("tested to", "test result shall show", "laboratory test required"):
  required_sources: ["test_report"]
  ← Spec explicitly requires a test — datasheet alone is insufficient

certificate ("certified to", "NSF certified", "CE marked", "approved to"):
  required_sources: ["test_report"]
  ← Use test_report to represent certificate/test evidence

standard with "comply with" (flexible compliance):
  required_sources: []
  optional_sources: ["technical_datasheet", "test_report"], minimum_optional_matches: 1

standard with "certified to" (certification explicitly required):
  required_sources: ["test_report"]

installation ("laid X mm above", "backfill", "installation procedure", "overlap"):
  required_sources: ["method_statement"]
  optional_sources: []
  DO NOT set technical_datasheet — a datasheet never describes installation procedures

experience ("years of use", "proven track record", "established history"):
  required_sources: []
  optional_sources: ["previous_approval", "others"], minimum_optional_matches: 1

warranty:
  required_sources: ["manufacturer_guarantee"]

administrative, procedural:
  required_sources: []
  optional_sources: [], minimum_optional_matches: 0

approval:
  required_sources: ["previous_approval"]

━━━ COMPARISON TABLE RULES ━━━
comparison_table_required: true  → dimension, standard, material, test, certificate, performance
comparison_table_required: false → installation, experience, administrative, warranty, procedural, approval, other
(Comparison tables list product properties — not installation procedures or administrative items)

━━━ GENERAL RULES ━━━
- One requirement per distinct checkable claim — do not bundle multiple specs
- For ranges "150–200 mm": operator="in_range", numeric_min=150, numeric_max=200, unit="mm"
- Skip vague general statements like "shall be in accordance with best practice"
- If no verifiable requirements found, return {"requirements": []}

Allowed DocType values: technical_datasheet, test_report, ded_registration,
manufacturer_guarantee, previous_approval, method_statement, maf, msdf,
specification_copy, comparison_table, others

Return JSON only: {"requirements": [...]}"""


_VERIFY_SYSTEM = """You verify construction material submittal documents against specification requirements.

For each requirement ID, search the provided submitted document text and return:
- requirement_id: matches input requirement
- status: "satisfied" | "non_compliant" | "partially_verified" | "missing_evidence" | "not_applicable"
- verification_method: "numeric_comparison" | "string_match" | "semantic_match" | "certificate_validation" | "date_validation" | "llm_reasoning"
- confidence: 0.0-1.0
- evidence_found: list of {document_type, source_document, page, text, extracted_value}
  - Only cite text you actually see in the provided documents — never fabricate
  - extracted_value: parsed numeric (e.g. 6.3) if applicable, else null
  - page: 0 if page number not visible in text
- missing_evidence: list of DocType strings where evidence was expected but not found in any document
- contradictions: list of evidence items that contradict the requirement (same format as evidence_found)
- reasoning: one or two sentences explaining the finding

━━━ COMPARISON_TABLE_ROWS IS AUTHORITATIVE EVIDENCE ━━━
The section named [COMPARISON_TABLE_ROWS] contains structured data extracted directly from the
contractor's submitted comparison table — it is pre-parsed, not raw OCR text.
  - "measured" column = independently measured/tested value from lab or on-site
  - "proposed" column = manufacturer's declared value from their datasheet
  - Both are valid evidence for product property requirements
If a measured or proposed value in COMPARISON_TABLE_ROWS satisfies a requirement,
you MUST report it as evidence_found (document_type: "comparison_table") and set
status to satisfied or partially_verified — do NOT report missing_evidence for that requirement.
Treat these rows exactly as you would treat explicit numbers in the raw document text.

━━━ MULTI-SOURCE SEARCH RULE ━━━
Search EVERY provided document for each requirement — never stop after checking one source.
For product properties (thickness, tensile strength, elongation, softening point, tear resistance):
  1. Check COMPARISON_TABLE_ROWS first — measured and proposed values are already extracted
  2. Then search the technical_datasheet
  3. Then search the test_report
  4. Only if the value is absent from ALL of the above → report missing_evidence
A value found in the test_report is valid evidence even if the datasheet does not mention it.
Do NOT report "missing_evidence" for a numeric property if the value appears in any provided document.
Report ALL evidence snippets found across all documents in evidence_found.

━━━ VERIFICATION GUIDANCE ━━━
- numeric: find the value in ANY document, report extracted_value — Python performs the pass/fail
  evaluation after you return, so do NOT assess against the operator yourself. Your job is only
  to locate the value and report extracted_value accurately.
- standard reference: search all documents for the standard code (e.g. "BS EN 805") with a compliance claim
- certificate: is a certificate document present that explicitly covers this certification?
- material type/grade: does the datasheet confirm material and grade?
- installation requirement (method_statement expected): is a method statement provided?
  If no method statement is in the provided documents → missing_evidence: ["method_statement"]
- "not_applicable": only use when the requirement clearly does not apply to this material

EVIDENCE QUALITY NOTE: Test report evidence is stronger than datasheet for performance properties.
When both are found and agree → satisfied, confidence: 0.95+.
When only datasheet found → partially_verified, confidence: 0.70.
When only test report found → satisfied, confidence: 0.90.

HONESTY RULE: Only cite text you actually read in the provided documents. Never fabricate snippets.
Only add a DocType to missing_evidence if that document type was expected (in required_sources or
optional_sources) AND after searching all provided text you found no relevant evidence for it.

Return JSON only: {"verifications": [...]}"""


# ── Phase 1 — Validate submitted specification copy ────────────────────────────

def _phase1_validate_index2(
    store: SubmittalKnowledgeStore,
    spec_clause: str,
) -> list[Finding]:
    """
    Check that Index 2 (SPECIFICATION_COPY) is present and references the
    correct clause number.  Returns a list of findings (may be empty if clean).
    """
    findings: list[Finding] = []

    if not store.has_type(DocType.SPECIFICATION_COPY):
        findings.append(Finding(
            stage="spec_verification",
            document="index_2_specification",
            description="Specification copy (Index 2) is missing from the submittal.",
            severity=Severity.CRITICAL,
            action_required="Include a copy of the relevant specification clause(s) as Index 2.",
        ))
        return findings

    spec_copy_text = store.get_text(DocType.SPECIFICATION_COPY).lower()
    # Strip common prefixes to get the raw number, e.g. "02810" from "ADM Specs Div-02-Section 02810"
    from src.rag.query.query_constructor import normalize_clause_ref
    normalized = normalize_clause_ref(spec_clause)

    if normalized and normalized not in spec_copy_text:
        findings.append(Finding(
            stage="spec_verification",
            document="index_2_specification",
            description=(
                f"Submitted specification copy does not appear to reference clause {spec_clause}. "
                f"Verify the correct specification section was included."
            ),
            severity=Severity.WARNING,
            action_required="Confirm Index 2 contains the specification clause cited on the cover page.",
        ))

    return findings


# ── Phase 2 — Extract requirements from authority spec ─────────────────────────

@traceable(name="spec_verifier_extract_requirements")
def _phase2_extract_requirements(
    spec_context: str,
    spec_clause: str,
    material_description: str,
    authority: str,
    submittal_id: str,
) -> ReviewRequirementsArtifact:
    """
    Call the LLM to extract structured SpecRequirement objects from the
    retrieved authority spec context.  The LLM determines evidence_expectation
    from the spec wording — not from a hardcoded type→evidence table.
    """
    from src.rag.query.query_constructor import normalize_clause_ref

    user_msg = (
        f"Authority: {authority}\n"
        f"Clause reference: {spec_clause}\n"
        f"Material: {material_description}\n\n"
        f"SPECIFICATION TEXT:\n{spec_context}\n\n"
        "Extract all verifiable requirements from this specification text."
    )

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    raw_requirements: list[SpecRequirement] = []
    try:
        parsed = json.loads(response.choices[0].message.content)
        for item in parsed.get("requirements", []):
            try:
                ev_raw = item.get("expected_value", {})
                ev = ExpectedValue(
                    text=ev_raw.get("text"),
                    operator=ev_raw.get("operator"),
                    numeric_min=ev_raw.get("numeric_min"),
                    numeric_max=ev_raw.get("numeric_max"),
                    unit=ev_raw.get("unit"),
                )
                ee_raw = item.get("evidence_expectation", {})
                ee = EvidenceExpectation(
                    required_sources=[
                        DocType(s) for s in ee_raw.get("required_sources", [])
                        if s in DocType._value2member_map_
                    ],
                    optional_sources=[
                        DocType(s) for s in ee_raw.get("optional_sources", [])
                        if s in DocType._value2member_map_
                    ],
                    minimum_optional_matches=ee_raw.get("minimum_optional_matches", 0),
                )
                req = SpecRequirement(
                    id=item.get("id", f"R-{len(raw_requirements)+1:03d}"),
                    requirement_type=RequirementType(
                        item.get("requirement_type", "other")
                        if item.get("requirement_type") in RequirementType._value2member_map_
                        else "other"
                    ),
                    normalized_requirement=item.get("normalized_requirement", ""),
                    expected_value=ev,
                    evidence_expectation=ee,
                    source_clause=item.get("source_clause", ""),
                    source_page=item.get("source_page", 0),
                    source_text=item.get("source_text", ""),
                    mandatory=item.get("mandatory", True),
                    comparison_table_required=item.get("comparison_table_required", True),
                )
                raw_requirements.append(req)
            except Exception:
                continue
    except Exception:
        pass

    return ReviewRequirementsArtifact(
        submittal_id=submittal_id,
        authority=authority,
        spec_clause=spec_clause,
        normalized_clause=normalize_clause_ref(spec_clause),
        material_description=material_description,
        requirements=raw_requirements,
        extraction_model=_MODEL,
    )


# ── Phase 3 — Verify requirements against submitted documents ──────────────────

def _build_evidence_block(
    store: SubmittalKnowledgeStore,
    needed_types: set[DocType],
) -> str:
    """
    Assemble submitted document text into a labelled block for the LLM.

    Two evidence sources are included:

    1. Pre-parsed comparison table rows — structured data with Specified/Proposed/
       Measured/Deviation columns already extracted by doc_processor.  This is the
       same data Stage 9 (table_auditor) uses, so Stage 3 now sees the same measured
       values (e.g. tensile=1130N) without depending on raw PDF text extraction.

    2. Raw document text — covers properties not captured in the comparison table,
       and non-numeric evidence (standard references, material descriptions, certificates).
       Truncated to _MAX_DOC_CHARS per type — enough for several PDF pages.
    """
    parts: list[str] = []

    # ── Structured comparison table rows (same data Stage 9 uses) ─────────────
    if store.table_rows:
        row_lines: list[str] = []
        for row_dict in store.table_rows:
            p = row_dict.get("parameter", "")
            sp = row_dict.get("specified", "")
            pr = row_dict.get("proposed", "")
            me = row_dict.get("measured", "")
            dev = row_dict.get("deviation", "")
            rem = row_dict.get("remarks", "")
            if p:
                row_lines.append(
                    f"  {p}: specified={sp!r} | proposed={pr!r} | measured={me!r}"
                    + (f" | deviation={dev!r}" if dev else "")
                    + (f" | remarks={rem!r}" if rem else "")
                )
        if row_lines:
            parts.append(
                "[COMPARISON_TABLE_ROWS] pre-parsed structured data "
                "(Specified / Proposed / Measured values from contractor's comparison table)\n"
                + "\n".join(row_lines)
            )

    # ── Raw document text ──────────────────────────────────────────────────────
    always_include = {DocType.TECHNICAL_DATASHEET, DocType.TEST_REPORT, DocType.COMPARISON_TABLE}
    types_to_include = needed_types | always_include

    for doc_type in types_to_include:
        text = store.get_text(doc_type).strip()
        if not text:
            continue
        label = doc_type.value.upper()
        sections = [s for s in store.sections if s.doc_type == doc_type]
        filenames = ", ".join(dict.fromkeys(s.filename for s in sections))
        parts.append(
            f"[{label}] {filenames}\n"
            f"{text[:_MAX_DOC_CHARS]}"
            + (" ...[truncated]" if len(text) > _MAX_DOC_CHARS else "")
        )

    return "\n\n---\n\n".join(parts) if parts else ""


_MATCH_THRESHOLD = 60  # rapidfuzz token_set_ratio: 0–100, 60 is enough for parameter names


def _match_table_row(
    req: SpecRequirement,
    table_rows: list[dict],
) -> float | None:
    """
    Search store.table_rows for a row whose parameter matches this requirement.
    Returns the best numeric value found (measured preferred over proposed), or None.

    Uses rapidfuzz.token_set_ratio so that word-order differences and partial
    overlaps ("Tensile Strength" vs "Longitudinal tensile strength") are handled
    correctly — a single shared word like "strength" alone is not enough.
    """
    import re as _re
    from rapidfuzz import fuzz

    req_text = req.normalized_requirement.lower()

    best_score = 0
    best_row: dict | None = None
    for row in table_rows:
        param = row.get("parameter", "").lower()
        if not param:
            continue
        score = fuzz.token_set_ratio(req_text, param)
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None or best_score < _MATCH_THRESHOLD:
        return None

    # Prefer measured value (independently tested) over proposed (declared)
    for col in ("measured", "proposed"):
        raw = best_row.get(col, "").strip()
        if raw:
            m = _re.search(r"[-+]?\d+(?:\.\d+)?", raw.replace(",", "."))
            if m:
                return float(m.group())
    return None


def _apply_deterministic_overrides(
    req_by_id: dict[str, SpecRequirement],
    verifications: list[RequirementVerification],
    table_rows: list[dict] | None = None,
) -> list[RequirementVerification]:
    """
    Override LLM results with deterministic Python math for numeric requirements.

    Two-pass strategy:
    1. If the LLM returned evidence_found with extracted_value → use it (LLM found the value).
    2. If evidence_found is empty → search store.table_rows directly with Python keyword
       matching, exactly as Stage 9 does.  This ensures numeric requirements are never
       left as missing_evidence just because the LLM failed to locate the value in the
       prompt text.

    Python calls expected_value.check(actual) — confidence 1.0 always.
    """
    # Source quality ranking — higher = more authoritative
    _SOURCE_PRIORITY = {
        DocType.TEST_REPORT:         3,
        DocType.COMPARISON_TABLE:    2,
        DocType.TECHNICAL_DATASHEET: 1,
    }

    result: list[RequirementVerification] = []
    for v in verifications:
        req = req_by_id.get(v.requirement_id)
        if req and req.expected_value.is_numeric():
            # Pass 1: LLM-returned evidence — pick highest-priority source
            numeric_evidence = [
                e for e in v.evidence_found if e.extracted_value is not None
            ]
            numeric_evidence.sort(
                key=lambda e: _SOURCE_PRIORITY.get(e.document_type, 0),
                reverse=True,
            )
            actual: float | None = numeric_evidence[0].extracted_value if numeric_evidence else None

            # Pass 2: direct Python search of table rows when LLM found nothing
            if actual is None and table_rows:
                actual = _match_table_row(req, table_rows)
                if actual is not None:
                    # Synthesize an evidence snippet so downstream consumers can see the source
                    synthetic = EvidenceSnippet(
                        document_type=DocType.COMPARISON_TABLE,
                        source_document="comparison_table (structured rows)",
                        page=0,
                        text=f"Parameter matched in comparison table rows: value={actual}",
                        extracted_value=actual,
                    )
                    v = v.model_copy(update={"evidence_found": list(v.evidence_found) + [synthetic]})

            if actual is not None:
                passes = req.expected_value.check(actual)
                v = v.model_copy(update={
                    "status": VerificationStatus.SATISFIED if passes else VerificationStatus.NON_COMPLIANT,
                    "verification_method": VerificationMethod.NUMERIC_COMPARISON,
                    "confidence": 1.0,
                    "reasoning": (
                        f"{actual} {req.expected_value.unit or ''} "
                        f"{req.expected_value.operator} "
                        f"{req.expected_value.numeric_min} {req.expected_value.unit or ''} "
                        f"→ {'PASS' if passes else 'FAIL'} (deterministic)"
                    ),
                })
        result.append(v)
    return result


_TEXT_VERIFIABLE_TYPES = {
    RequirementType.STANDARD,
    RequirementType.MATERIAL,
    RequirementType.CERTIFICATE,
}


def _apply_text_overrides(
    req_by_id: dict[str, SpecRequirement],
    verifications: list[RequirementVerification],
    evidence_text: str,
) -> list[RequirementVerification]:
    """
    Deterministic string-match check for standard codes, material grades, and certifications.

    Logic mirrors _apply_deterministic_overrides for numeric requirements:
    - If expected_value.text is found verbatim in the evidence (case-insensitive) →
      override to string_match / satisfied, confidence 0.85.
    - If NOT found → leave the LLM result unchanged.  The LLM may have matched a
      semantic equivalent or different phrasing that regex would miss (e.g. "BS EN ISO
      13252" vs "BS EN 13252") — do not downgrade a satisfied result on a regex miss.

    Covers:
      STANDARD   — "BS EN 13252", "ASTM D638", "ISO 9001"
      MATERIAL   — "HDPE PE100", "Grade 60", "PE80"
      CERTIFICATE — "NSF 61", "CE marking", "WRAS approved"
    """
    import re as _re

    result: list[RequirementVerification] = []
    for v in verifications:
        req = req_by_id.get(v.requirement_id)
        if req and req.requirement_type in _TEXT_VERIFIABLE_TYPES:
            search_text = (req.expected_value.text or "").strip()
            if search_text:
                # Search for full phrase first; if the text contains spaces, also try
                # each individual token so "HDPE PE100" matches docs that only say "PE100"
                patterns_to_try: list[str] = [search_text]
                tokens = search_text.split()
                if len(tokens) > 1:
                    patterns_to_try.extend(tokens)

                matched: str | None = None
                for candidate in patterns_to_try:
                    # Skip tokens that are too generic (≤ 2 chars, pure digits)
                    if len(candidate) <= 2 or candidate.isdigit():
                        continue
                    if _re.search(_re.escape(candidate), evidence_text, _re.IGNORECASE):
                        matched = candidate
                        break

                if matched:
                    v = v.model_copy(update={
                        "status": VerificationStatus.SATISFIED,
                        "verification_method": VerificationMethod.STRING_MATCH,
                        "confidence": 0.85,
                        "reasoning": (
                            f"'{matched}' found in submitted documents "
                            "(deterministic string match)."
                        ),
                    })
        result.append(v)
    return result


@traceable(name="spec_verifier_verify_requirements")
def _phase3_verify_requirements(
    requirements_artifact: ReviewRequirementsArtifact,
    store: SubmittalKnowledgeStore,
) -> RequirementVerificationArtifact:
    """
    For each extracted SpecRequirement, search the submitted documents and
    produce a RequirementVerification.  One LLM call handles all requirements
    together so the model can cross-reference across documents.

    For numeric requirements, deterministic Python comparison overrides the
    LLM's judgment once an extracted_value is found.
    """
    requirements = requirements_artifact.requirements
    if not requirements:
        return RequirementVerificationArtifact(
            submittal_id=requirements_artifact.submittal_id,
            authority=requirements_artifact.authority,
            spec_clause=requirements_artifact.spec_clause,
            material_description=requirements_artifact.material_description,
            verifications=[],
        )

    # Collect document types needed across all requirements
    needed_types: set[DocType] = set()
    for req in requirements:
        needed_types.update(req.evidence_expectation.required_sources)
        needed_types.update(req.evidence_expectation.optional_sources)

    evidence_block = _build_evidence_block(store, needed_types)

    # Build requirements summary for the prompt
    req_lines: list[str] = []
    for req in requirements:
        req_lines.append(
            f"{req.id}: {req.normalized_requirement}\n"
            f"  type: {req.requirement_type.value}\n"
            f"  expected: {req.expected_value.text or ''}"
            + (f" ({req.expected_value.operator} {req.expected_value.numeric_min}"
               f"{(' – ' + str(req.expected_value.numeric_max)) if req.expected_value.numeric_max else ''}"
               f" {req.expected_value.unit or ''})" if req.expected_value.is_numeric() else "")
            + f"\n  required_sources: {[d.value for d in req.evidence_expectation.required_sources]}"
            + f"\n  optional_sources: {[d.value for d in req.evidence_expectation.optional_sources]}"
              f" (min {req.evidence_expectation.minimum_optional_matches})"
        )

    user_msg = (
        f"Material: {requirements_artifact.material_description}\n"
        f"Spec clause: {requirements_artifact.spec_clause}\n\n"
        f"REQUIREMENTS TO VERIFY:\n{''.join(req_lines)}\n\n"
        f"SUBMITTED DOCUMENTS:\n{evidence_block if evidence_block else '[No relevant documents found in submittal]'}\n\n"
        "Verify each requirement against the submitted documents above."
    )

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _VERIFY_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    verifications: list[RequirementVerification] = []
    try:
        parsed = json.loads(response.choices[0].message.content)
        for item in parsed.get("verifications", []):
            try:
                evidence_found = [
                    EvidenceSnippet(
                        document_type=DocType(e.get("document_type", "others")),
                        source_document=e.get("source_document", ""),
                        page=e.get("page", 0),
                        text=e.get("text", ""),
                        extracted_value=e.get("extracted_value"),
                    )
                    for e in item.get("evidence_found", [])
                    if e.get("document_type") in DocType._value2member_map_
                ]
                contradictions = [
                    EvidenceSnippet(
                        document_type=DocType(e.get("document_type", "others")),
                        source_document=e.get("source_document", ""),
                        page=e.get("page", 0),
                        text=e.get("text", ""),
                        extracted_value=e.get("extracted_value"),
                    )
                    for e in item.get("contradictions", [])
                    if e.get("document_type") in DocType._value2member_map_
                ]
                missing = [
                    DocType(d) for d in item.get("missing_evidence", [])
                    if d in DocType._value2member_map_
                ]
                status_val = item.get("status", "missing_evidence")
                method_val = item.get("verification_method", "llm_reasoning")
                verifications.append(RequirementVerification(
                    requirement_id=item.get("requirement_id", ""),
                    requirement_summary=next(
                        (r.normalized_requirement for r in requirements
                         if r.id == item.get("requirement_id")),
                        item.get("requirement_id", ""),
                    ),
                    status=VerificationStatus(status_val)
                    if status_val in VerificationStatus._value2member_map_
                    else VerificationStatus.MISSING_EVIDENCE,
                    verification_method=VerificationMethod(method_val)
                    if method_val in VerificationMethod._value2member_map_
                    else VerificationMethod.LLM_REASONING,
                    confidence=float(item.get("confidence", 0.5)),
                    evidence_found=evidence_found,
                    missing_evidence=missing,
                    contradictions=contradictions,
                    reasoning=item.get("reasoning", ""),
                ))
            except Exception:
                continue
    except Exception:
        pass

    req_by_id = {r.id: r for r in requirements}
    verifications = _apply_deterministic_overrides(req_by_id, verifications, table_rows=store.table_rows)
    verifications = _apply_text_overrides(req_by_id, verifications, evidence_block)

    # Add a MISSING_EVIDENCE entry for any requirement the LLM didn't return
    verified_ids = {v.requirement_id for v in verifications}
    for req in requirements:
        if req.id not in verified_ids:
            verifications.append(RequirementVerification(
                requirement_id=req.id,
                requirement_summary=req.normalized_requirement,
                status=VerificationStatus.MISSING_EVIDENCE,
                verification_method=VerificationMethod.LLM_REASONING,
                confidence=0.0,
                reasoning="Verification result not returned by LLM.",
            ))

    return RequirementVerificationArtifact(
        submittal_id=requirements_artifact.submittal_id,
        authority=requirements_artifact.authority,
        spec_clause=requirements_artifact.spec_clause,
        material_description=requirements_artifact.material_description,
        verifications=verifications,
    )


# ── Convert to legacy Finding format for report compiler ──────────────────────

_STATUS_TO_SEVERITY = {
    VerificationStatus.SATISFIED:          Severity.PASS,
    VerificationStatus.NON_COMPLIANT:      Severity.CRITICAL,
    VerificationStatus.PARTIALLY_VERIFIED: Severity.WARNING,
    VerificationStatus.MISSING_EVIDENCE:   Severity.WARNING,
    VerificationStatus.NOT_APPLICABLE:     Severity.PASS,
}


def _verification_to_finding(v: RequirementVerification) -> Finding:
    severity = _STATUS_TO_SEVERITY.get(v.status, Severity.WARNING)

    if v.status == VerificationStatus.SATISFIED:
        action = "No action required."
    elif v.status == VerificationStatus.NON_COMPLIANT:
        action = "Resubmit with compliant material or provide evidence of compliance."
    elif v.status == VerificationStatus.NOT_APPLICABLE:
        action = "No action required."
    else:
        missing = ", ".join(d.value for d in v.missing_evidence) if v.missing_evidence else "relevant documents"
        action = f"Provide evidence from: {missing}."

    return Finding(
        stage="spec_verification",
        document="submittal_package",
        description=f"[{v.requirement_id}] {v.requirement_summary} — {v.reasoning}",
        severity=severity,
        action_required=action,
    )


# ── Main node ──────────────────────────────────────────────────────────────────

@traceable(name="spec_verifier_agent")
def spec_verifier_node(state: SubmittalReviewState) -> dict:
    """
    Agent 2 — Spec Verifier (three-phase compliance engine).

    Phase 1: Validate that the submitted specification copy (Index 2) is
             present and references the correct clause.

    Phase 2: Retrieve the authority spec from ChromaDB and extract structured
             SpecRequirement objects — the source-of-truth requirement list.

    Phase 3: For each requirement, search the submitted documents for evidence
             and produce a RequirementVerification.  Numeric requirements are
             verified with deterministic Python math, not LLM judgment.

    Outputs stored in state:
      requirements_artifact  — ReviewRequirementsArtifact.model_dump()
      verification_artifact  — RequirementVerificationArtifact.model_dump()
      spec_verification_findings — list[Finding.model_dump()] for report compiler
    """
    authority: str = state.get("authority", "ADM")
    store = load_store(state["knowledge_store_id"])
    spec_clause: str = store.spec_clause
    material_description: str = store.material_description
    submittal_id: str = state.get("submittal_id", store.submittal_id)

    # ── Missing clause — cannot proceed ───────────────────────────────────────
    if not spec_clause:
        finding = Finding(
            stage="spec_verification",
            document="cover_page",
            description="Specification clause reference not found on cover page. Spec verification skipped.",
            severity=Severity.WARNING,
            action_required="Ensure the cover page contains a valid specification clause reference.",
        )
        return {**state, "spec_verification_findings": [finding.model_dump()]}

    # ── Phase 1 ────────────────────────────────────────────────────────────────
    phase1_findings = _phase1_validate_index2(store, spec_clause)

    # ── Retrieve authority spec context (enriched with material + submitted spec) ─
    # Snippet from submitted spec copy gives the embedding strong extra signal:
    # subsection numbers, standard references, and technical terms the contractor
    # used narrow the semantic search far better than the clause number alone.
    spec_copy_snippet = store.get_text(DocType.SPECIFICATION_COPY)[:500]
    spec_context = assemble_spec_context_enriched(
        clause_ref=spec_clause,
        authority=authority,
        material_description=material_description,
        spec_snippet=spec_copy_snippet,
    )

    if spec_context == EMPTY_CONTEXT_SENTINEL:
        finding = Finding(
            stage="spec_verification",
            document="spec_database",
            description=(
                f"Clause {spec_clause} was not found in the authority specification database. "
                "Requirement extraction skipped."
            ),
            severity=Severity.WARNING,
            action_required="Verify clause reference is correct and ensure authority spec has been indexed.",
        )
        all_findings = phase1_findings + [finding]
        return {**state, "spec_verification_findings": [f.model_dump() for f in all_findings]}

    # ── Phase 2 ────────────────────────────────────────────────────────────────
    requirements_artifact = _phase2_extract_requirements(
        spec_context=spec_context,
        spec_clause=spec_clause,
        material_description=material_description,
        authority=authority,
        submittal_id=submittal_id,
    )

    if not requirements_artifact.requirements:
        finding = Finding(
            stage="spec_verification",
            document="spec_database",
            description=(
                f"No verifiable requirements could be extracted from clause {spec_clause}. "
                "The retrieved spec context may be incomplete."
            ),
            severity=Severity.WARNING,
            action_required="Review indexed spec content for this clause.",
        )
        all_findings = phase1_findings + [finding]
        return {
            **state,
            "requirements_artifact": requirements_artifact.model_dump(),
            "spec_verification_findings": [f.model_dump() for f in all_findings],
        }

    # ── Phase 3 ────────────────────────────────────────────────────────────────
    verification_artifact = _phase3_verify_requirements(requirements_artifact, store)

    # Convert to legacy Finding format for report compiler
    verification_findings = [
        _verification_to_finding(v).model_dump()
        for v in verification_artifact.verifications
    ]

    all_findings = [f.model_dump() for f in phase1_findings] + verification_findings

    return {
        **state,
        "requirements_artifact": requirements_artifact.model_dump(),
        "verification_artifact": verification_artifact.model_dump(),
        "spec_verification_findings": all_findings,
    }
