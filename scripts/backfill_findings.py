"""
One-off backfill for migration 006 (findings table, Ticket 1). Every COMPLETED submittal
already has its findings inside submittals.report JSONB — the exact same shape
apps/worker/findings.py::extract_findings() consumes for new reviews — so existing reviews
don't need to be re-run to get stable finding IDs; this just derives them from data
already on disk.

Skips any submittal that already has findings rows (idempotent — safe to re-run, e.g. after
interrupting a partial run). Submittals stuck in a non-terminal status, or COMPLETED with a
null report (shouldn't happen, but report_compiler_node's contract isn't enforced by a DB
constraint), are skipped and counted separately rather than guessed at.

Run once: python scripts/backfill_findings.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from apps.worker.findings import extract_findings
from db.session import SyncSessionLocal

_INSERT_SQL = text(
    """
    INSERT INTO findings
        (id, tenant_id, project_id, submittal_id, category, severity,
         description, action_required, clause_reference, spec_document_id,
         spec_page, source_document_id, source_page, confidence,
         pipeline_node, model_version, prompt_version, pipeline_version)
    VALUES
        (:id, :tenant_id, :project_id, :submittal_id, :category, :severity,
         :description, :action_required, :clause_reference, :spec_document_id,
         :spec_page, :source_document_id, :source_page, :confidence,
         :pipeline_node, :model_version, :prompt_version, :pipeline_version)
    """
)


def main() -> None:
    with SyncSessionLocal() as db:
        candidates = (
            db.execute(
                text(
                    """
                    SELECT s.id, s.tenant_id, s.project_id, s.report, s.pipeline_version
                    FROM submittals s
                    WHERE s.status = 'COMPLETED'
                      AND NOT EXISTS (SELECT 1 FROM findings f WHERE f.submittal_id = s.id)
                    """
                )
            )
            .mappings()
            .fetchall()
        )

        backfilled = 0
        skipped_no_report = 0
        total_findings = 0

        for row in candidates:
            report = row["report"]
            if isinstance(report, str):  # driver-dependent JSONB decoding, same pattern as worker.py
                report = json.loads(report)
            if not report:
                skipped_no_report += 1
                print(f"  skipping {row['id']} — COMPLETED but report is empty/null")
                continue

            rows = extract_findings(
                report,
                tenant_id=str(row["tenant_id"]),
                project_id=str(row["project_id"]),
                submittal_id=str(row["id"]),
                pipeline_version=row["pipeline_version"] or "unknown",
            )
            if rows:
                db.execute(_INSERT_SQL, rows)
            backfilled += 1
            total_findings += len(rows)
            print(f"  backfilled {row['id']} — {len(rows)} findings")

        db.commit()

    print(
        f"\nDone. {backfilled} submittals backfilled ({total_findings} findings total), "
        f"{skipped_no_report} skipped (no report)."
    )


if __name__ == "__main__":
    main()
