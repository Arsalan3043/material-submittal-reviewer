from __future__ import annotations

import streamlit as st

from src.agents.doc_processor import stage_files
from src.config import get_authority_profile
from src.config.paths import SUBMITTALS_DIR
from src.models.submittal import SubmittalMetadata
from src.parsers.file_io import save_upload


def render() -> None:
    st.title("Upload Submittal Package")

    # ── Review setup ──────────────────────────────────────────────────────────
    st.subheader("1. Review Setup")

    col1, col2 = st.columns(2)
    with col1:
        authority = st.selectbox(
            "Authority",
            options=["ADM", "TAQA"],
            index=0 if st.session_state.get("authority", "ADM") == "ADM" else 1,
        )
        st.session_state.authority = authority

    with col2:
        project_name = st.text_input(
            "Project Name (optional)",
            placeholder="e.g. Al Reem Island Residential Tower",
        )

    # ── File upload ───────────────────────────────────────────────────────────
    st.subheader("2. Upload Documents")
    st.caption(
        "Upload individual PDFs (one per document type) **or** a single bundled PDF "
        "containing the full submittal package. Bundled PDFs over 20 pages are split "
        "automatically."
    )

    uploaded_files = st.file_uploader(
        "Select PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if not uploaded_files:
        st.info("Upload one or more PDF files to continue.")
        return

    # ── Per-file labels ───────────────────────────────────────────────────────
    st.subheader("3. Document Labels")
    st.caption(
        "Declare what each file contains. This helps detect mislabelled documents. "
        "Leave as **Auto-detect** if unsure — the system classifies automatically."
    )

    profile = get_authority_profile(authority)
    label_options: list[str] = ["Auto-detect"] + [
        f"Index {i}: {name}" for i, name in profile.index_items.items()
    ]

    declared_labels: dict[str, str | None] = {}

    for f in uploaded_files:
        raw = f.getvalue()
        size_kb = len(raw) / 1024
        size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"

        col_name, col_label = st.columns([2, 3])
        with col_name:
            st.markdown(f"**{f.name}**  \n`{size_str}`")
        with col_label:
            choice = st.selectbox(
                label="label",
                options=label_options,
                key=f"label__{f.name}",
                label_visibility="collapsed",
            )
        declared_labels[f.name] = None if choice == "Auto-detect" else choice

    # ── Start review ──────────────────────────────────────────────────────────
    st.divider()

    if not project_name:
        st.caption("Project name is optional — you can leave it blank.")

    if st.button("Start Review", type="primary", use_container_width=True):
        # Clear any previous review so the sidebar gates reset properly.
        st.session_state.review_complete = False
        st.session_state.report = None
        st.session_state.conversation_history = []

        metadata = SubmittalMetadata(authority=authority, project_name=project_name)
        submittal_id = metadata.submittal_id

        # Save uploaded bytes to data/submittals/{submittal_id}/ for reproducibility.
        file_contents: dict[str, bytes] = {}
        dest = SUBMITTALS_DIR / submittal_id
        for f in uploaded_files:
            data = f.getvalue()
            save_upload(dest, f.name, data)
            file_contents[f.name] = data

        # Deposit bytes into the staging store — doc_processor pops them during
        # the graph run. PDF bytes never enter LangGraph state or LangSmith traces.
        stage_files(submittal_id, file_contents, declared_labels)

        st.session_state.metadata = metadata.model_dump()
        st.session_state.page = "review"
        st.rerun()
