from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.avl_checker import avl_checker_node
from src.agents.consistency_checker import consistency_checker_node
from src.agents.doc_processor import doc_processor_node
from src.agents.report_compiler import report_compiler_node
from src.agents.spec_verifier import spec_verifier_node
from src.agents.state import SubmittalReviewState
from src.agents.table_auditor import table_auditor_node
from src.agents.validity_checker import validity_checker_node
from src.models.submittal import ClassifiedDocument
from src.rules.completeness import check_completeness
from src.config import get_authority_profile


def _completeness_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """Inline node: completeness check runs between doc_processor and spec_verifier."""
    authority = state.get("authority", "ADM")
    profile = get_authority_profile(authority)
    classified = {
        fn: ClassifiedDocument.model_validate(d)
        for fn, d in state.get("classified_documents", {}).items()
    }
    findings, missing = check_completeness(classified, profile)
    return {
        **state,
        "completeness_findings": [f.model_dump() for f in findings],
        "missing_documents": missing,
    }


def _boq_drawing_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Placeholder node: BOQ/drawing material-type check.
    Returns an empty findings list — full implementation goes here when drawings
    and BOQ are provided in a format that allows reliable parsing.
    """
    return {**state, "boq_drawing_findings": []}


def _statement_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Placeholder node: compliance statement audit.
    Returns an empty findings list — full implementation added in a later phase.
    """
    return {**state, "statement_findings": []}


def _others_node(state: SubmittalReviewState) -> SubmittalReviewState:
    """
    Placeholder node: review of documents classified as OTHERS.
    Returns an empty findings list — implementation added in a later phase.
    """
    return {**state, "others_findings": []}


def _should_run_avl(state: SubmittalReviewState) -> str:
    """Conditional edge: run AVL check only for TAQA, skip for ADM."""
    return "avl_check" if state.get("authority") == "TAQA" else "skip_avl"


def _skip_avl_node(state: SubmittalReviewState) -> SubmittalReviewState:
    return {**state, "avl_findings": []}


def build_review_graph() -> StateGraph:
    """
    Build the LangGraph state machine for the full 9-stage review pipeline.

    All stages always execute regardless of findings — the review is never
    stopped early. Stages run sequentially; parallelism is not used here
    because each stage may depend on classified_documents from stage 1.

    LangGraph 0.1.19 API: StateGraph + add_node + add_edge + add_conditional_edges.
    """
    graph = StateGraph(SubmittalReviewState)

    # Register all nodes
    graph.add_node("doc_processor",     doc_processor_node)
    graph.add_node("completeness",      _completeness_node)
    graph.add_node("boq_drawing",       _boq_drawing_node)
    graph.add_node("spec_verifier",     spec_verifier_node)
    graph.add_node("validity_checker",  validity_checker_node)
    graph.add_node("avl_check",         avl_checker_node)
    graph.add_node("skip_avl",          _skip_avl_node)
    graph.add_node("statement",         _statement_node)
    graph.add_node("table_auditor",     table_auditor_node)
    graph.add_node("consistency",       consistency_checker_node)
    graph.add_node("others",            _others_node)
    graph.add_node("report_compiler",   report_compiler_node)

    # Stage 1 → Stage 2
    graph.set_entry_point("doc_processor")
    graph.add_edge("doc_processor",     "completeness")
    graph.add_edge("completeness",      "boq_drawing")
    graph.add_edge("boq_drawing",       "spec_verifier")
    graph.add_edge("spec_verifier",     "validity_checker")

    # Conditional: TAQA → avl_check, ADM → skip_avl
    graph.add_conditional_edges(
        "validity_checker",
        _should_run_avl,
        {"avl_check": "avl_check", "skip_avl": "skip_avl"},
    )

    # Both AVL paths reconverge at statement audit
    graph.add_edge("avl_check",         "statement")
    graph.add_edge("skip_avl",          "statement")
    graph.add_edge("statement",         "table_auditor")
    graph.add_edge("table_auditor",     "consistency")
    graph.add_edge("consistency",       "others")
    graph.add_edge("others",            "report_compiler")
    graph.add_edge("report_compiler",   END)

    return graph


def compile_review_graph():
    """Return a compiled (runnable) LangGraph app for the review pipeline."""
    return build_review_graph().compile()
