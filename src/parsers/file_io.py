from __future__ import annotations

from pathlib import Path


def load_pdf_bytes(path: str | Path) -> bytes:
    """
    Read a PDF from local disk and return raw bytes.

    This is the single choke-point for all file I/O in the pipeline.
    In production, swap the body to download from S3/Blob Storage — nothing
    else in the codebase needs to change.
    """
    return Path(path).read_bytes()


def save_upload(dest_dir: str | Path, filename: str, data: bytes) -> Path:
    """
    Save bytes from a Streamlit file_uploader to disk.
    Returns the absolute path of the saved file.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / filename
    out.write_bytes(data)
    return out
