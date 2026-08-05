"""apps/worker/worker.py::run_review — the Phase 0 seam proven against a real Postgres
transaction (worker_app role) but zero network calls: S3 is monkeypatched and the pipeline
is FakeReviewPipeline. Requires a reachable dev database (DATABASE_URL/.env), same as every
other test that touched db/session.py before this file existed — marked `integration`
because of that DB dependency, not because it spends real money (it never calls OpenAI)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from adapters.pipeline.fake_pipeline import FakeReviewPipeline
from apps.worker import worker as worker_module
from core.errors import PipelineTransientError
from core.models import ReviewResult
from db.session import SyncSessionLocal

pytestmark = pytest.mark.integration

TEST_S3_BUCKET = "test-bucket-unused"


@pytest.fixture
def seeded_submittal(monkeypatch: pytest.MonkeyPatch):
    """Creates a throwaway tenant/user/project/submittal/submittal_files graph, monkeypatches
    S3 download to avoid any real network call, and cleans everything up afterward."""
    monkeypatch.setattr(
        worker_module, "_load_pdf_bytes_typed", lambda uri: b"%PDF-fake content"
    )

    try:
        with SyncSessionLocal() as db:
            tenant_id = uuid.uuid4()
            user_id = uuid.uuid4()
            project_id = uuid.uuid4()
            submittal_id = uuid.uuid4()

            db.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Test Tenant', :slug)"),
                {"id": tenant_id, "slug": f"test-tenant-{tenant_id.hex[:8]}"},
            )
            db.execute(
                text(
                    "INSERT INTO users (id, tenant_id, cognito_sub, email, role) "
                    "VALUES (:id, :tid, :sub, 'test@example.com', 'reviewer')"
                ),
                {"id": user_id, "tid": tenant_id, "sub": f"sub-{user_id.hex[:8]}"},
            )
            db.execute(
                text(
                    "INSERT INTO projects (id, tenant_id, name, authority, created_by) "
                    "VALUES (:id, :tid, 'Test Project', 'ADM', :uid)"
                ),
                {"id": project_id, "tid": tenant_id, "uid": user_id},
            )
            db.execute(
                text(
                    "INSERT INTO submittals (id, tenant_id, project_id, user_id, status) "
                    "VALUES (:id, :tid, :pid, :uid, 'CREATED')"
                ),
                {"id": submittal_id, "tid": tenant_id, "pid": project_id, "uid": user_id},
            )
            db.execute(
                text(
                    "INSERT INTO submittal_files (submittal_id, tenant_id, original_name, "
                    "declared_label, s3_key) VALUES (:sid, :tid, 'datasheet.pdf', 'datasheet', "
                    "'unused/key.pdf')"
                ),
                {"sid": submittal_id, "tid": tenant_id},
            )
            db.commit()
    except OperationalError as exc:
        pytest.skip(f"no reachable Postgres for integration test: {exc}")

    yield submittal_id

    with SyncSessionLocal() as db:
        db.execute(text("DELETE FROM findings WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittal_events WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittal_files WHERE submittal_id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM submittals WHERE id=:id"), {"id": submittal_id})
        db.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
        db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
        db.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        db.commit()


def test_run_review_completes_and_persists_report(seeded_submittal: uuid.UUID) -> None:
    submittal_id = seeded_submittal
    with SyncSessionLocal() as db:
        worker_module.run_review(db, submittal_id, TEST_S3_BUCKET, FakeReviewPipeline())

    with SyncSessionLocal() as db:
        row = (
            db.execute(
                text(
                    "SELECT status, recommendation, report IS NOT NULL AS has_report "
                    "FROM submittals WHERE id=:id"
                ),
                {"id": submittal_id},
            )
            .mappings()
            .fetchone()
        )
        assert row is not None
        assert row["status"] == "COMPLETED"
        assert row["recommendation"] == "APPROVE"
        assert row["has_report"] is True

        event_count = db.execute(
            text("SELECT count(*) FROM submittal_events WHERE submittal_id=:id"),
            {"id": submittal_id},
        ).scalar()
        assert event_count == 11


def test_run_review_propagates_pipeline_error_without_marking_completed(
    seeded_submittal: uuid.UUID,
) -> None:
    submittal_id = seeded_submittal
    failing_pipeline = FakeReviewPipeline(raises=PipelineTransientError("simulated LLM outage"))

    with SyncSessionLocal() as db, pytest.raises(PipelineTransientError):
        worker_module.run_review(db, submittal_id, TEST_S3_BUCKET, failing_pipeline)

    with SyncSessionLocal() as db:
        status = db.execute(
            text("SELECT status FROM submittals WHERE id=:id"), {"id": submittal_id}
        ).scalar()
        # run_review itself doesn't catch pipeline errors — that's _handle_job's job
        # (apps/worker/worker.py::run_worker's except block). Here we're only proving
        # run_review leaves the row at RUNNING, not silently COMPLETED, on failure.
        assert status == "RUNNING"


def _report_with_findings() -> dict:
    return {
        "submittal_id": "irrelevant",
        "authority": "ADM",
        "material_description": "HDPE pipe",
        "spec_clause": "10.2.2",
        "review_date": "2026-08-05",
        "completeness_findings": [
            {
                "stage": "completeness",
                "document": "Submittal package",
                "description": "Missing required document: MSDF.",
                "severity": "critical",
                "action_required": "Include MSDF in resubmission.",
            }
        ],
        "boq_drawing_findings": [],
        "spec_verification_findings": [
            {
                "stage": "spec_verifier",
                "document": "submittal_package",
                "description": "[REQ-1] PN rating — matches spec.",
                "severity": "pass",
                "action_required": "No action required.",
            }
        ],
        "validity_findings": [],
        "avl_findings": [],
        "statement_findings": [],
        "table_audit_findings": [
            {
                "parameter": "PN rating",
                "specified_value": "PN16",
                "proposed_value": "PN10",
                "deviation_declared": "none",
                "measured_value": "PN10",
                "specified_correct": True,
                "proposed_verified": True,
                "measured_verified": True,
                "deviation_accurate": False,
                "missing_from_spec": False,
                "finding": "Proposed PN rating below spec requirement.",
                "severity": "warning",
            }
        ],
        "consistency_findings": [],
        "others_findings": [],
        "critical_count": 1,
        "warning_count": 1,
        "missing_documents": ["MSDF"],
        "overall_recommendation": "RESUBMIT",
        "summary_comments": "Test report with real findings.",
    }


def test_run_review_persists_findings_with_stable_ids_in_the_same_transaction(
    seeded_submittal: uuid.UUID,
) -> None:
    submittal_id = seeded_submittal
    pipeline = FakeReviewPipeline(result=ReviewResult(report=_report_with_findings()))

    with SyncSessionLocal() as db:
        worker_module.run_review(db, submittal_id, TEST_S3_BUCKET, pipeline)

    with SyncSessionLocal() as db:
        rows = (
            db.execute(
                text(
                    "SELECT id, category, severity, description, pipeline_node, "
                    "pipeline_version, tenant_id, project_id, submittal_id "
                    "FROM findings WHERE submittal_id=:id ORDER BY category"
                ),
                {"id": submittal_id},
            )
            .mappings()
            .fetchall()
        )

    assert len(rows) == 3  # 1 completeness + 1 spec_verification + 1 table_audit
    by_category = {r["category"]: r for r in rows}

    assert by_category["completeness"]["severity"] == "critical"
    assert by_category["completeness"]["pipeline_node"] == "completeness"
    assert by_category["spec_verification"]["severity"] == "observation"
    assert by_category["table_audit"]["severity"] == "warning"
    assert by_category["table_audit"]["description"] == "Proposed PN rating below spec requirement."

    for row in rows:
        assert row["submittal_id"] == submittal_id
        assert row["pipeline_version"] == worker_module.PIPELINE_VERSION

    # Restart-and-reload: re-fetching by the same IDs returns identical rows — the
    # acceptance criterion from notes/11_pilot_bar_tickets.md Ticket 1.
    first_ids = sorted(str(r["id"]) for r in rows)
    with SyncSessionLocal() as db:
        reread = (
            db.execute(
                text("SELECT id FROM findings WHERE submittal_id=:id"), {"id": submittal_id}
            )
            .mappings()
            .fetchall()
        )
    assert sorted(str(r["id"]) for r in reread) == first_ids


def test_run_review_with_no_findings_inserts_nothing(seeded_submittal: uuid.UUID) -> None:
    submittal_id = seeded_submittal
    with SyncSessionLocal() as db:
        worker_module.run_review(db, submittal_id, TEST_S3_BUCKET, FakeReviewPipeline())

    with SyncSessionLocal() as db:
        count = db.execute(
            text("SELECT count(*) FROM findings WHERE submittal_id=:id"), {"id": submittal_id}
        ).scalar()
    assert count == 0
