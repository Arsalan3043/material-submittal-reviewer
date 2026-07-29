"""
One-off dev utility: proves the Phase 1/2 stack (Postgres schema, S3, job queue,
worker) works end to end with real data, before Phase 3 (the API) exists to do
this for you. Not part of the production app — a manual stand-in for what
"POST /projects/{id}/submittals" + "POST /submittals/{id}/start" will do later.

What it does:
  1. Uploads the 3 indexed ADM spec PDFs to S3 under specs/ADM/{network}/
  2. Uploads one real test submittal's PDFs to S3 under {tenant}/{project}/{submittal}/
  3. Seeds tenants/users/projects/spec_documents/project_specs/submittals/
     submittal_files/jobs with fixed, printed UUIDs so runs are reproducible
     and easy to inspect afterward.

Run once: python scripts/dev_seed_review.py
Then:     python -m apps.worker.worker
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import boto3
from sqlalchemy import text

from db.session import SyncSessionLocal

REPO_ROOT = Path(__file__).parent.parent
S3_BUCKET = os.environ["S3_BUCKET_NAME"]
AWS_REGION = os.environ.get("AWS_REGION")

# Fixed IDs — reproducible across re-runs of this script.
TENANT_ID = uuid.UUID("6962777a-39aa-4fee-9242-a1ee431fb7c0")
USER_ID = uuid.UUID("8a8e05d8-86eb-4000-9348-7a997e6174d2")
PROJECT_ID = uuid.UUID("2da04a0a-6fb1-4c09-9d0d-6d7cb5fbf861")
SUBMITTAL_ID = uuid.UUID("32b79c90-3d27-4d59-b764-a12d3627ddb3")

SPECS = [
    ("irrigation", REPO_ROOT / "specs" / "ADM" / "irrigation.pdf"),
    ("road", REPO_ROOT / "specs" / "ADM" / "road.pdf"),
    ("storm_water", REPO_ROOT / "specs" / "ADM" / "storm_water.pdf"),
]

# One real irrigation submittal — matches the indexed irrigation spec above.
SUBMITTAL_DIR = REPO_ROOT / "Test Submittal" / "RHINF1-NPC-IR-MAT-0001_00"


def s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def upload_specs(client) -> dict[str, str]:
    """Returns {network: s3_key}."""
    keys: dict[str, str] = {}
    for network, path in SPECS:
        key = f"specs/ADM/{network}/{path.name}"
        print(f"  uploading {path.name} -> s3://{S3_BUCKET}/{key}")
        client.upload_file(str(path), S3_BUCKET, key)
        keys[network] = key
    return keys


def upload_submittal_files(client) -> list[tuple[str, str]]:
    """Returns [(original_name, s3_key), ...]."""
    files = sorted(p for p in SUBMITTAL_DIR.glob("*.pdf"))
    if not files:
        raise SystemExit(f"No PDFs found in {SUBMITTAL_DIR}")
    results = []
    for path in files:
        key = f"{TENANT_ID}/{PROJECT_ID}/{SUBMITTAL_ID}/{path.name}"
        print(f"  uploading {path.name} -> s3://{S3_BUCKET}/{key}")
        client.upload_file(str(path), S3_BUCKET, key)
        results.append((path.name, key))
    return results


def seed_db(spec_keys: dict[str, str], submittal_files: list[tuple[str, str]]) -> None:
    with SyncSessionLocal() as db:
        db.execute(
            text("""
                INSERT INTO tenants (id, name, slug)
                VALUES (:id, 'Dev Tenant', 'dev-tenant')
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": TENANT_ID},
        )
        db.execute(
            text("""
                INSERT INTO users (id, tenant_id, cognito_sub, email, role)
                VALUES (:id, :tenant_id, 'local-dev-user', 'dev@local.test', 'tenant_admin')
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": USER_ID, "tenant_id": TENANT_ID},
        )
        db.execute(
            text("""
                INSERT INTO projects (id, tenant_id, name, authority, created_by)
                VALUES (:id, :tenant_id, 'Dev Project', 'ADM', :created_by)
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": PROJECT_ID, "tenant_id": TENANT_ID, "created_by": USER_ID},
        )

        for network, s3_key in spec_keys.items():
            spec_doc_id = uuid.uuid5(uuid.NAMESPACE_URL, f"spec:ADM:{network}")
            db.execute(
                text("""
                    INSERT INTO spec_documents
                        (id, authority, network_name, source_file, source_s3_key, qdrant_collection)
                    VALUES (:id, 'ADM', :network, :source_file, :s3_key, 'adm_specifications')
                    ON CONFLICT (authority, network_name, source_file) DO NOTHING
                """),
                {
                    "id": spec_doc_id,
                    "network": network,
                    "source_file": Path(s3_key).name,
                    "s3_key": s3_key,
                },
            )
            db.execute(
                text("""
                    INSERT INTO project_specs (project_id, tenant_id, spec_document_id)
                    VALUES (:project_id, :tenant_id, :spec_document_id)
                    ON CONFLICT (project_id, spec_document_id) DO NOTHING
                """),
                {"project_id": PROJECT_ID, "tenant_id": TENANT_ID, "spec_document_id": spec_doc_id},
            )

        db.execute(
            text("""
                INSERT INTO submittals (id, tenant_id, project_id, user_id, material_desc, status)
                VALUES (:id, :tenant_id, :project_id, :user_id, 'Dev seed test submittal', 'CREATED')
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": SUBMITTAL_ID,
                "tenant_id": TENANT_ID,
                "project_id": PROJECT_ID,
                "user_id": USER_ID,
            },
        )

        for original_name, s3_key in submittal_files:
            db.execute(
                text("""
                    INSERT INTO submittal_files (submittal_id, tenant_id, original_name, s3_key)
                    VALUES (:submittal_id, :tenant_id, :original_name, :s3_key)
                """),
                {
                    "submittal_id": SUBMITTAL_ID,
                    "tenant_id": TENANT_ID,
                    "original_name": original_name,
                    "s3_key": s3_key,
                },
            )

        job_row = db.execute(
            text("""
                INSERT INTO jobs (job_type, submittal_id, status)
                VALUES ('review', :submittal_id, 'PENDING')
                RETURNING id
            """),
            {"submittal_id": SUBMITTAL_ID},
        ).fetchone()

        db.commit()
        print(f"\n  job queued: {job_row[0]}")


def main() -> None:
    client = s3_client()

    print("Uploading spec PDFs to S3...")
    spec_keys = upload_specs(client)

    print("\nUploading submittal PDFs to S3...")
    submittal_files = upload_submittal_files(client)

    print("\nSeeding Postgres...")
    seed_db(spec_keys, submittal_files)

    print("\nDone. IDs for reference:")
    print(f"  tenant_id    = {TENANT_ID}")
    print(f"  project_id   = {PROJECT_ID}")
    print(f"  submittal_id = {SUBMITTAL_ID}")
    print("\nNext: python -m apps.worker.worker")


if __name__ == "__main__":
    main()
