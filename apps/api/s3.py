"""
Presigned S3 URLs — the frontend PUTs files directly to S3 using these, never through the
API process itself (planning/05_build_plan_for_claude_code.md Phase 3: "the frontend PUTs
files directly to S3"). Server-side AWS credentials never leave this process.
"""
from __future__ import annotations

import os

import boto3

S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
AWS_REGION = os.environ.get("AWS_REGION")

_client = boto3.client("s3", region_name=AWS_REGION)


def presigned_put_url(
    key: str, expires_in: int = 3600, content_type: str = "application/pdf"
) -> str:
    """
    content_type is signed into the URL — S3's presigned-URL scheme includes Content-Type
    in what it signs, so the browser's actual PUT must send this exact header or S3 rejects
    it with SignatureDoesNotMatch (confirmed with a real request while debugging the upload
    flow — the browser auto-attaches Content-Type from the File object's .type, so the
    frontend must set this explicitly to match rather than let the browser choose). Every
    upload in this app is a PDF, so a fixed default is correct, not a simplification that
    will bite later.
    """
    return _client.generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_BUCKET_NAME, "Key": key, "ContentType": content_type},
        ExpiresIn=expires_in,
    )


def presigned_get_url(key: str, expires_in: int = 3600) -> str:
    """
    For citation links — the frontend appends "#page=N" itself (see
    apps/api/routers/submittals.py::_build_citations). Safe to do without re-signing:
    URL fragments are stripped by the browser before the HTTP request is ever made, so they
    never touch S3's signature check at all.
    """
    return _client.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET_NAME, "Key": key},
        ExpiresIn=expires_in,
    )
