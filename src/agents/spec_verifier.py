from __future__ import annotations

import json

from langsmith import traceable
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.findings import Finding, Severity
from src.models.knowledge_store import load_store
from src.models.submittal import DocType
from src.rag.query.context_assembler import EMPTY_CONTEXT_SENTINEL, assemble_spec_context

_MODEL = "gpt-4o-mini"
_MAX_SPEC_CHARS = 3000

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


_SYSTEM_PROMPT = """You are auditing a submitted specification section against the actual authority specification.

Your job is to identify:
1. Wrong clause — submitted clause number does not match what was referenced on the cover page.
2. Incomplete section — submitted copy is missing required sub-clauses or cuts off prematurely.
3. Wrong values — submitted specification shows different values than the authority specification.
4. Correct — submitted specification matches authority specification adequately.

Return JSON only:
{
  "findings": [
    {
      "issue_type": "wrong_clause" | "incomplete_section" | "wrong_values" | "correct",
      "description": "...",
      "severity": "critical" | "warning" | "pass",
      "action_required": "..."
    }
  ]
}

Rules:
- If spec context is not available, return a single warning finding explaining the clause was not found in the database.
- Minor formatting differences are not findings.
- Only flag genuine content discrepancies.
- Return at least one finding (use "correct" / "pass" if everything checks out)."""


@traceable(name="spec_verifier_agent")
def spec_verifier_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Agent 2 — Spec Verifier.

    Retrieves the stored authority spec clause for the cover page clause reference,
    then compares the submitted specification (Index 2) against it.
    """
    authority: str = state.get("authority", "ADM")
    store = load_store(state["knowledge_store_id"])
    spec_clause: str = store.spec_clause

    findings: list[dict] = []

    if not spec_clause:
        findings.append(Finding(
            stage="spec_verification",
            document="cover_page",
            description="Specification clause reference not found on cover page. Cannot perform spec verification.",
            severity=Severity.WARNING,
            action_required="Ensure cover page contains a valid specification clause reference.",
        ).model_dump())
        return {**state, "spec_verification_findings": findings}

    # Retrieve authority spec context for this clause
    spec_context = assemble_spec_context(clause_ref=spec_clause, authority=authority)

    if spec_context == EMPTY_CONTEXT_SENTINEL:
        findings.append(Finding(
            stage="spec_verification",
            document="spec_database",
            description=f"Clause {spec_clause} was not found in the authority specification database. Verification skipped.",
            severity=Severity.WARNING,
            action_required="Verify clause reference is correct. Ensure authority spec has been indexed.",
        ).model_dump())
        return {**state, "spec_verification_findings": findings}

    # Find the submitted specification copy (Index 2) — already extracted by doc_processor
    spec_copy_text = store.get_text(DocType.SPECIFICATION_COPY)

    if not spec_copy_text:
        findings.append(Finding(
            stage="spec_verification",
            document="index_2_specification",
            description="No specification copy (Index 2) found in submittal. Cannot verify against authority spec.",
            severity=Severity.CRITICAL,
            action_required="Include a copy of the relevant specification clause(s) as Index 2.",
        ).model_dump())
        return {**state, "spec_verification_findings": findings}

    user_msg = (
        f"AUTHORITY SPEC (retrieved from database):\n{spec_context}\n\n"
        f"SUBMITTED SPEC (Index 2 from submittal):\n{spec_copy_text[:_MAX_SPEC_CHARS]}\n\n"
        f"Cover page clause reference: {spec_clause}\n\n"
        "Identify any discrepancies between what was submitted and what the authority spec actually requires."
    )

    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        parsed = json.loads(response.choices[0].message.content)
        raw_findings = parsed.get("findings", [])
    except Exception:
        raw_findings = []

    for f in raw_findings:
        severity_map = {"critical": Severity.CRITICAL, "warning": Severity.WARNING, "pass": Severity.PASS}
        findings.append(Finding(
            stage="spec_verification",
            document="index_2_specification",
            description=f.get("description", ""),
            severity=severity_map.get(f.get("severity", "warning"), Severity.WARNING),
            action_required=f.get("action_required", "Review and correct."),
        ).model_dump())

    if not findings:
        findings.append(Finding(
            stage="spec_verification",
            document="index_2_specification",
            description=f"Submitted specification for clause {spec_clause} appears consistent with authority spec.",
            severity=Severity.PASS,
            action_required="No action required.",
        ).model_dump())

    return {**state, "spec_verification_findings": findings}
