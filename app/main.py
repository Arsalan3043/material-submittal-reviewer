import streamlit as st

from src.config.paths import ensure_dirs

st.set_page_config(
    page_title="Material Submittal Reviewer",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Create data/chromadb, data/knowledge_stores, data/specs, data/submittals on first run.
ensure_dirs()

# ── Session state defaults ────────────────────────────────────────────────────
# All pages read/write these keys. Define defaults here once.
#
# Session state contract:
#   authority           str                "ADM" or "TAQA"
#   metadata            dict | None        SubmittalMetadata.model_dump()
#   review_complete     bool               True once the LangGraph run finishes
#   knowledge_store_id  str | None         Path to the SubmittalKnowledgeStore JSON
#   report              dict | None        ReviewReport.model_dump()
#   conversation_history list[dict]        ConversationTurn dicts for query mode
_DEFAULTS: dict = {
    "page":                 "upload",
    "authority":            "ADM",
    "metadata":             None,
    "review_complete":      False,
    "knowledge_store_id":   None,
    "report":               None,
    "conversation_history": [],
}
for _key, _val in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val

# ── Sidebar navigation ────────────────────────────────────────────────────────
# Pages marked as gated are disabled until a review has been completed.
_NAV = [
    ("upload",       "Upload Submittal", False),
    ("review",       "Review Progress",  True),
    ("report",       "Report",           True),
    ("chat",         "Query Mode",       True),
    ("spec_manager", "Spec Manager",     False),
]

with st.sidebar:
    st.markdown("### Material Submittal Reviewer")
    st.divider()

    for page_key, label, gated in _NAV:
        disabled = gated and not st.session_state.review_complete
        if st.button(label, key=f"nav_{page_key}", disabled=disabled, use_container_width=True):
            st.session_state.page = page_key
            st.rerun()

    st.divider()
    st.caption(f"Authority: **{st.session_state.authority}**")
    if st.session_state.metadata:
        sid = st.session_state.metadata.get("submittal_id", "")
        st.caption(f"Submittal: `...{sid[-8:]}`")
    if not st.session_state.review_complete:
        st.caption("Review Progress, Report, and Query Mode unlock after a review completes.")

# ── Page routing ──────────────────────────────────────────────────────────────
_page = st.session_state.page

if _page == "upload":
    from app.pages.upload import render
    render()
elif _page == "review":
    from app.pages.review import render
    render()
elif _page == "report":
    from app.pages.report import render
    render()
elif _page == "chat":
    from app.pages.chat import render
    render()
elif _page == "spec_manager":
    from app.pages.spec_manager import render
    render()
