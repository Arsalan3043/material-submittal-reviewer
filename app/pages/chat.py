from __future__ import annotations

import streamlit as st

from src.agents.query_agent import handle_query

_SOURCE_LABELS = {
    "spec_rag":      "Authority Specification",
    "submittal_rag": "Submitted Documents",
    "report_json":   "Review Report",
}

_SUGGESTED_QUESTIONS = [
    "What is the overall recommendation and why?",
    "Which documents are missing from this submittal?",
    "What critical issues were found in the comparison table?",
    "What does the specification require for this material?",
    "Are there any expired certificates or documents?",
]


def _build_query_state() -> dict:
    """Assemble the minimal SubmittalReviewState dict the query agent needs."""
    metadata    = st.session_state.get("metadata", {})
    report_dict = st.session_state.get("report", {})
    return {
        "authority":            metadata.get("authority", "ADM"),
        "submittal_id":         metadata.get("submittal_id", ""),
        "spec_clause":          report_dict.get("spec_clause", ""),
        "report":               report_dict,
        "conversation_history": list(st.session_state.get("conversation_history", [])),
    }


def _render_turn(question: str, answer: dict) -> None:
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        st.write(answer["answer"])

        source      = answer.get("source", "")
        source_label = _SOURCE_LABELS.get(source, source)
        confidence  = answer.get("confidence", "")
        refs        = answer.get("source_references", [])

        caption_parts = [f"Source: **{source_label}**", f"Confidence: {confidence}"]
        if refs:
            caption_parts.append("References: " + ", ".join(refs))
        st.caption("  |  ".join(caption_parts))


def render() -> None:
    st.title("Query Mode")

    # ── Guard ─────────────────────────────────────────────────────────────────
    if not st.session_state.get("review_complete"):
        st.warning("Query mode is available after a review completes.")
        if st.button("Go to Upload"):
            st.session_state.page = "upload"
            st.rerun()
        return

    metadata = st.session_state.get("metadata", {})
    st.caption(
        f"Submittal `{metadata.get('submittal_id', '')[:8]}...`  |  "
        f"Authority: **{metadata.get('authority', '—')}**  |  "
        f"Ask about the review findings, submitted documents, or specification requirements."
    )

    # ── Suggested questions (only shown when history is empty) ────────────────
    history: list[dict] = st.session_state.get("conversation_history", [])

    if not history:
        st.markdown("**Suggested questions:**")
        for q in _SUGGESTED_QUESTIONS:
            if st.button(q, key=f"sq__{q[:30]}"):
                st.session_state["_pending_question"] = q
                st.rerun()

    # ── Render conversation history ───────────────────────────────────────────
    for turn in history:
        _render_turn(turn["question"], turn["answer"])

    # ── Handle pending question from suggestion buttons ───────────────────────
    pending = st.session_state.pop("_pending_question", None)

    # ── Chat input ────────────────────────────────────────────────────────────
    prompt = st.chat_input("Ask about this submittal or review findings...")
    question = prompt or pending

    if question:
        # Show the user message immediately before the spinner appears.
        with st.chat_message("user"):
            st.write(question)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                state        = _build_query_state()
                updated      = handle_query(state, question)
                new_history  = updated["conversation_history"]

            # The last turn is the one just added.
            last_turn = new_history[-1]
            answer    = last_turn["answer"]
            st.write(answer["answer"])

            source_label = _SOURCE_LABELS.get(answer.get("source", ""), answer.get("source", ""))
            confidence   = answer.get("confidence", "")
            refs         = answer.get("source_references", [])
            caption_parts = [f"Source: **{source_label}**", f"Confidence: {confidence}"]
            if refs:
                caption_parts.append("References: " + ", ".join(refs))
            st.caption("  |  ".join(caption_parts))

        # Persist history and rerun so the history list reflects the new turn.
        st.session_state.conversation_history = new_history
        st.rerun()

    # ── Clear chat ────────────────────────────────────────────────────────────
    if history:
        st.divider()
        if st.button("Clear Chat", use_container_width=False):
            st.session_state.conversation_history = []
            st.rerun()
