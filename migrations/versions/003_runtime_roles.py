"""create api_app (RLS-constrained) and worker_app (BYPASSRLS) runtime roles

Revision ID: 003
Revises: 002
Create Date: 2026-07-24

Root cause of a real cross-tenant data leak found while testing Phase 3: the API and the
worker both connected to Postgres as the `postgres` superuser (docker-compose's default
user, same DATABASE_URL for both). Superusers ALWAYS bypass Row Level Security, full stop
— FORCE ROW LEVEL SECURITY (migration 001) only affects the table OWNER's own bypass, it
does nothing for superusers. So every RLS policy from migration 001 was silently a no-op
for both the API and the worker the whole time; it only ever actually got exercised by the
one-off `app_user` role created manually during Phase 1 testing (never migrated, lost the
moment the dev docker volume was recreated) — which is exactly why this went undetected
until a real second-tenant test against the live API surfaced it.

Fix: two distinct, non-superuser runtime roles, cleanly separating what each process is
trusted to do:

  api_app    — the API's role. Regular privileges, NOT BYPASSRLS. RLS actually constrains
               every query this role makes — the defense-in-depth layer that catches a
               route that forgot a WHERE tenant_id=... clause. This is the role that
               matters most: it's the one an external HTTP request ultimately runs as.

  worker_app — the worker's role. BYPASSRLS granted deliberately: the worker must read a
               submittal's own tenant_id from the submittals table before it can know
               what to `SET LOCAL app.tenant_id` to — a chicken-and-egg problem plain RLS
               can't solve for a trusted backend process (see db/session.py's docstring).

Migrations themselves keep running as the `postgres` superuser (DDL — CREATE TABLE,
CREATE ROLE — needs elevated privileges neither runtime role has or should have).

Dev-only passwords: hardcoded below deliberately, since this is a local Docker Postgres
instance with no shared secrets infrastructure yet. On RDS, rotate both via `ALTER ROLE
... PASSWORD ...` post-deploy (or manage through Secrets Manager) — never rely on a
migration file's password reaching production.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DB_NAME = "material_submittal_reviewer"


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_app') THEN
                CREATE ROLE api_app LOGIN PASSWORD 'api_app_dev_password';
            END IF;
        END
        $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'worker_app') THEN
                CREATE ROLE worker_app LOGIN PASSWORD 'worker_app_dev_password' BYPASSRLS;
            END IF;
        END
        $$;
    """)

    for role in ("api_app", "worker_app"):
        op.execute(f"GRANT CONNECT ON DATABASE {_DB_NAME} TO {role}")
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        # Applies to tables created by future migrations too, without a follow-up grant.
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        )


def downgrade() -> None:
    for role in ("api_app", "worker_app"):
        op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}")
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
        op.execute(f"REVOKE CONNECT ON DATABASE {_DB_NAME} FROM {role}")
        op.execute(f"DROP ROLE IF EXISTS {role}")
