from __future__ import annotations

from datetime import date

import streamlit as st

from src.agents.orchestrator import compile_review_graph
from src.models.findings import ReviewReport


@st.cache_resource
def _get_graph():
    """Compile the LangGraph review graph once per process — not per Streamlit rerun."""
    return compile_review_graph()


# Human-readable label for every graph node, in execution order.
_NODE_LABELS: dict[str, str] = {
    "doc_processor":    "Stage 1  —  Classify & Extract Documents",
    "completeness":     "Stage 2  —  Completeness Check",
    "boq_drawing":      "Stage 3  —  BOQ & Drawing Check",
    "spec_verifier":    "Stage 4  —  Spec Verification",
    "validity_checker": "Stage 5  —  Validity & Date Checks",
    "avl_check":        "Stage 6  —  AVL Check",
    "skip_avl":         "Stage 6  —  AVL Check (not required for ADM)",
    "statement":        "Stage 7  —  Compliance Statement Audit",
    "table_auditor":    "Stage 8  —  Comparison Table Audit",
    "consistency":      "Stage 9  —  Consistency Check",
    "others":           "Others  —  Additional Document Review",
    "report_compiler":  "Compiling Final Report",
}


def _show_summary() -> None:
    """Show the post-review summary card. Called when review_complete is True."""
    report_dict = st.session_state.get("report")
    if not report_dict:
        st.warning("Review is marked complete but no report was found. Re-upload and retry.")
        return

    report = ReviewReport.model_validate(report_dict)
    rec = report.overall_recommendation

    if rec == "APPROVE":
        st.success(f"Overall Recommendation: **{rec}**")
    elif rec == "CONDITIONAL":
        st.warning(f"Overall Recommendation: **{rec}**")
    else:
        st.error(f"Overall Recommendation: **{rec}**")

    col1, col2, col3 = st.columns(3)
    col1.metric("Critical Findings", report.critical_count)
    col2.metric("Warnings",          report.warning_count)
    col3.metric("Missing Documents", len(report.missing_documents))

    if report.summary_comments:
        st.info(report.summary_comments)

    st.divider()

    col_report, col_upload = st.columns(2)
    with col_report:
        if st.button("View Full Report", type="primary", use_container_width=True):
            st.session_state.page = "report"
            st.rerun()
    with col_upload:
        if st.button("Start New Review", use_container_width=True):
            # Clear review state so upload page starts fresh.
            st.session_state.review_complete      = False
            st.session_state.report               = None
            st.session_state.metadata             = None
            st.session_state.knowledge_store_id   = None
            st.session_state.conversation_history = []
            st.session_state.page = "upload"
            st.rerun()


def render() -> None:
    st.title("Review Progress")

    # ── Guard: nothing uploaded yet ───────────────────────────────────────────
    metadata = st.session_state.get("metadata")
    if not metadata:
        st.warning("No submittal uploaded. Please upload a package first.")
        if st.button("Go to Upload"):
            st.session_state.page = "upload"
            st.rerun()
        return

    submittal_id = metadata["submittal_id"]
    authority    = metadata.get("authority", "ADM")
    project_name = metadata.get("project_name", "")

    caption = f"Submittal `{submittal_id[:8]}...`  |  Authority: **{authority}**"
    if project_name:
        caption += f"  |  {project_name}"
    st.caption(caption)

    # ── Guard: review already finished (page revisit) ─────────────────────────
    if st.session_state.get("review_complete"):
        st.success("Review complete.")
        _show_summary()
        return

    # ── Run the graph ─────────────────────────────────────────────────────────
    initial_state: dict = {
        "authority":    authority,
        "submittal_id": submittal_id,
        "review_date":  date.today().isoformat(),
    }

    graph = _get_graph()
    completed_nodes: list[str] = []
    # Accumulate state across events — nodes return {**state, delta} so each event
    # is a full-state snapshot, but accumulating guards against LangGraph versions
    # that stream only deltas.
    accumulated: dict = {}

    with st.status(
        "Running review — this may take a few minutes...", expanded=True
    ) as status:
        try:
            for event in graph.stream(initial_state):
                node_name  = next(iter(event))
                node_state = event[node_name]
                accumulated.update(node_state)

                label = _NODE_LABELS.get(node_name, node_name)
                st.write(f"Completed: {label}")
                completed_nodes.append(node_name)

            status.update(label="Review complete.", state="complete", expanded=False)

        except Exception as exc:
            last = completed_nodes[-1] if completed_nodes else "unknown"
            status.update(label="Review failed.", state="error", expanded=True)
            st.error(
                f"Error after **{_NODE_LABELS.get(last, last)}**: {exc}"
            )
            st.caption(
                "Check the LangSmith trace for details. "
                "Fix the issue and re-upload the submittal to retry."
            )
            return

    # ── Persist results and trigger summary render ────────────────────────────
    report_dict = accumulated.get("report")
    if report_dict:
        st.session_state.report = report_dict

    knowledge_store_id = accumulated.get("knowledge_store_id")
    if knowledge_store_id:
        st.session_state.knowledge_store_id = knowledge_store_id

    st.session_state.review_complete = True
    # Rerun so the page re-enters through the guard above and renders _show_summary().
    st.rerun()
