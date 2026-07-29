from __future__ import annotations

import os
from pathlib import Path


def load_pdf_bytes(path: str | Path) -> bytes:
    """
    Read a PDF and return raw bytes.

    Single choke-point for all file reads in the pipeline. Local filesystem paths
    (used by the existing Streamlit app) are read from disk exactly as before.
    Paths of the form "s3://bucket/key" (used only by apps/worker/, which never
    touches local disk) are downloaded from S3.

    save_upload() below is intentionally left untouched, not swapped to S3:
    app/pages/spec_manager.py passes its return value straight into
    src/rag/indexing/indexer.py::index_spec_pdf(), which opens it with PyMuPDF and
    requires a real filesystem path. Switching save_upload to S3 would silently
    break that flow, which CLAUDE.md requires stays working for internal testing.
    """
    path_str = str(path)
    if path_str.startswith("s3://"):
        return _load_from_s3(path_str)
    return Path(path).read_bytes()


def _load_from_s3(s3_uri: str) -> bytes:
    import boto3  # lazy import: the Streamlit-only local-disk path never needs boto3 installed

    bucket, _, key = s3_uri.removeprefix("s3://").partition("/")
    client = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    obj = client.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


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
