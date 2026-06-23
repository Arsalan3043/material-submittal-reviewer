from __future__ import annotations

from pathlib import Path

import chromadb
import streamlit as st

from src.config.paths import CHROMA_PATH, SPECS_DIR
from src.parsers.file_io import save_upload
from src.rag.indexing.indexer import index_spec_pdf

_COLLECTIONS = {
    "ADM":  "adm_specifications",
    "TAQA": "taqa_specifications",
}


def _get_collection_status() -> dict[str, int]:
    """Return chunk count per authority collection. 0 if collection does not exist."""
    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
    except Exception:
        return {a: 0 for a in _COLLECTIONS}

    counts: dict[str, int] = {}
    for authority, col_name in _COLLECTIONS.items():
        try:
            counts[authority] = client.get_collection(col_name).count()
        except Exception:
            counts[authority] = 0
    return counts


def _get_indexed_networks(authority: str) -> list[str]:
    """Return the distinct network names already indexed for an authority."""
    col_name = _COLLECTIONS.get(authority)
    if not col_name:
        return []
    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        col    = client.get_collection(col_name)
        metas  = col.get(include=["metadatas"])["metadatas"] or []
        return sorted({m.get("network", "") for m in metas if m.get("network")})
    except Exception:
        return []


def render() -> None:
    st.title("Spec Manager")
    st.caption(
        "Index authority specification PDFs into the local ChromaDB knowledge base. "
        "This is an admin operation — run it once per spec book, or again when the spec is updated."
    )

    # ── Current collection status ─────────────────────────────────────────────
    st.subheader("Indexed Collections")

    status = _get_collection_status()
    col_adm, col_taqa = st.columns(2)

    with col_adm:
        n = status["ADM"]
        if n:
            st.success(f"**ADM**  —  {n:,} chunks indexed")
            nets = _get_indexed_networks("ADM")
            if nets:
                st.caption("Spec books: " + ", ".join(nets))
        else:
            st.warning("**ADM**  —  not indexed yet")

    with col_taqa:
        n = status["TAQA"]
        if n:
            st.success(f"**TAQA**  —  {n:,} chunks indexed")
            nets = _get_indexed_networks("TAQA")
            if nets:
                st.caption("Spec books: " + ", ".join(nets))
        else:
            st.warning("**TAQA**  —  not indexed yet")

    st.divider()

    # ── Upload and index ──────────────────────────────────────────────────────
    st.subheader("Index a New Spec PDF")

    col1, col2 = st.columns(2)
    with col1:
        authority = st.selectbox("Authority", options=["ADM", "TAQA"])
    with col2:
        network = st.text_input(
            "Spec Book Name",
            placeholder="e.g. Civil Works, Section 07",
            help=(
                "A short label for this spec book. Used internally to filter search results. "
                "Use the same name if adding multiple PDFs from the same book."
            ),
        )

    uploaded = st.file_uploader(
        "Select spec PDF",
        type=["pdf"],
        label_visibility="collapsed",
    )

    reset = st.checkbox(
        "Reset collection before indexing",
        value=False,
        help=(
            "Drops all existing chunks for this authority before adding new ones. "
            "Use this when replacing the entire spec, not when adding a new book."
        ),
    )

    if not uploaded:
        st.info("Upload a spec PDF to continue.")
        return

    if not network.strip():
        st.warning("Enter a Spec Book Name before indexing.")
        return

    size_mb = len(uploaded.getvalue()) / (1024 * 1024)
    st.caption(f"File: **{uploaded.name}**  —  {size_mb:.1f} MB")

    if size_mb > 50:
        st.warning(
            "Large spec file detected. Indexing may take 10–30 minutes depending on page count. "
            "Do not close this tab while indexing is running."
        )

    if st.button("Start Indexing", type="primary", use_container_width=True):
        # Save PDF to data/specs/{authority}/ for traceability.
        dest   = SPECS_DIR / authority.lower()
        saved  = save_upload(dest, uploaded.name, uploaded.getvalue())

        with st.status(
            f"Indexing {uploaded.name} into {authority} collection...", expanded=True
        ) as status_box:
            try:
                st.write("Parsing pages...")
                st.write("Structuring sections...")
                st.write("Chunking and embedding — this is the slow step...")

                n_chunks = index_spec_pdf(
                    pdf_path=saved,
                    authority=authority,
                    network=network.strip(),
                    reset=reset,
                )

                status_box.update(
                    label=f"Indexing complete — {n_chunks:,} chunks added.",
                    state="complete",
                    expanded=False,
                )
                st.success(
                    f"Indexed **{n_chunks:,} chunks** from `{uploaded.name}` "
                    f"into the **{authority}** collection under spec book **{network.strip()}**."
                )
                # Force a rerun so the status panel at the top refreshes.
                st.rerun()

            except Exception as exc:
                status_box.update(label="Indexing failed.", state="error", expanded=True)
                st.error(f"Error: {exc}")
                st.caption(
                    f"The PDF was saved to `{saved}`. "
                    "Fix the issue and try again — no chunks were added if the error occurred early."
                )
