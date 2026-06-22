from __future__ import annotations

import json

from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import OpenAI

from src.agents.state import SubmittalReviewState
from src.models.query import ConversationTurn, QueryAnswer, QuerySource
from src.rag.query.context_assembler import EMPTY_CONTEXT_SENTINEL, assemble_spec_context
from src.rag.submittal_rag.retriever import retrieve_from_submittal

_MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = wrap_openai(OpenAI())
    return _client


_ROUTE_SYSTEM_PROMPT = """You classify a user question about a material submittal review into one of three routing categories.

Categories:
- "spec_rag"       — question about what the authority specification requires (rules, standards, limits, clauses)
- "submittal_rag"  — question about the content of the submitted documents (what was proposed, datasheet values, test results)
- "report_json"    — question about the review findings, recommendation, or specific issues found during the review

Return JSON only: {"route": "spec_rag" | "submittal_rag" | "report_json"}"""


_ANSWER_SYSTEM_PROMPT = """You are answering a question about a UAE construction material submittal review.
Answer using ONLY the context provided. Do not invent facts.
If the context does not contain the answer, say: "I could not find this information in the available documents."
Always cite the source (document name) at the end of your answer.

Return JSON only:
{
  "answer": "...",
  "confidence": "high" | "medium" | "low",
  "source_references": ["document name or section"]
}"""


def _route_question(question: str) -> QuerySource:
    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _ROUTE_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        route = json.loads(response.choices[0].message.content).get("route", "submittal_rag")
        return QuerySource(route)
    except Exception:
        return QuerySource.SUBMITTAL_RAG


def _answer_from_context(question: str, context: str, source_label: str) -> QueryAnswer:
    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        parsed = json.loads(response.choices[0].message.content)
    except Exception:
        parsed = {}

    return QueryAnswer(
        answer=parsed.get("answer", "Unable to generate answer."),
        source=QuerySource(source_label),
        source_references=parsed.get("source_references", [source_label]),
        confidence=parsed.get("confidence", "low"),
    )


def _build_report_context(report: dict) -> str:
    """Flatten the report dict into a text summary for the LLM."""
    lines = [
        f"Recommendation: {report.get('overall_recommendation', 'unknown')}",
        f"Summary: {report.get('summary_comments', '')}",
        f"Critical issues: {report.get('critical_count', 0)}",
        f"Warnings: {report.get('warning_count', 0)}",
        f"Missing documents: {', '.join(report.get('missing_documents', [])) or 'none'}",
        "",
        "Key findings:",
    ]
    for key in (
        "completeness_findings",
        "spec_verification_findings",
        "validity_findings",
        "table_audit_findings",
        "consistency_findings",
    ):
        for f in report.get(key, []):
            desc = f.get("description") or f.get("finding", "")
            sev = f.get("severity", "")
            if desc:
                lines.append(f"  [{sev.upper()}] {desc}")
    return "\n".join(lines)


@traceable(name="query_agent")
def query_agent_node(state: SubmittalReviewState, question: str) -> QueryAnswer:
    """
    Agent 8 — Query Agent (post-review chat mode).

    Routes the question to the correct knowledge source and returns a grounded answer.
    Does not modify shared state — returns a QueryAnswer directly.
    """
    authority: str = state.get("authority", "ADM")
    submittal_id: str = state.get("submittal_id", "")
    spec_clause: str = state.get("spec_clause", "")
    report: dict = state.get("report", {})

    route = _route_question(question)

    if route == QuerySource.SPEC_RAG:
        raw_context = assemble_spec_context(
            question=question,
            clause_ref=spec_clause,
            authority=authority,
        )
        if raw_context == EMPTY_CONTEXT_SENTINEL:
            return QueryAnswer(
                answer="The specification clause was not found in the database. Please consult the authority specification document directly.",
                source=QuerySource.SPEC_RAG,
                source_references=["spec_database"],
                confidence="low",
            )
        return _answer_from_context(question, raw_context, QuerySource.SPEC_RAG)

    if route == QuerySource.SUBMITTAL_RAG:
        chunks = retrieve_from_submittal(question, submittal_id)
        if not chunks:
            return QueryAnswer(
                answer="No relevant content was found in the submitted documents for this question.",
                source=QuerySource.SUBMITTAL_RAG,
                source_references=[],
                confidence="low",
            )
        context = "\n\n".join(f"[{src}]\n{doc}" for doc, src in chunks)
        sources = list({src for _, src in chunks})
        answer = _answer_from_context(question, context, QuerySource.SUBMITTAL_RAG)
        return QueryAnswer(
            answer=answer.answer,
            source=QuerySource.SUBMITTAL_RAG,
            source_references=sources,
            confidence=answer.confidence,
        )

    # route == QuerySource.REPORT_JSON
    if not report:
        return QueryAnswer(
            answer="The review report is not yet available.",
            source=QuerySource.REPORT_JSON,
            source_references=["report"],
            confidence="low",
        )
    report_context = _build_report_context(report)
    return _answer_from_context(question, report_context, QuerySource.REPORT_JSON)


def handle_query(state: SubmittalReviewState, question: str) -> SubmittalReviewState:
    """
    Wraps query_agent_node to store the turn in conversation_history.
    Call this from the UI layer, not directly from the LangGraph graph.
    """
    answer = query_agent_node(state, question)
    turn = ConversationTurn(question=question, answer=answer)
    history = list(state.get("conversation_history", []))
    history.append(turn.model_dump())
    return {**state, "conversation_history": history}
