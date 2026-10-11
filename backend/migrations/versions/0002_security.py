"""Least-privilege application role, grants and row-level security.

- `regtrace_app` (LOGIN, NOSUPERUSER, NOBYPASSRLS) is what the application uses.
  Superusers and table owners bypass RLS, which is why the app must not use them.
- Every bank-scoped table gets a policy: visible/writable only when bank_id equals the
  transaction-local `app.bank_id` (or is NULL for shared public rows, where allowed).
- audit_log is append-only for the app role (INSERT/SELECT only).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.config import get_settings
from app.db.base import TENANT_TABLES

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "regtrace_app"
_TENANT = "NULLIF(current_setting('app.bank_id', true), '')::uuid"


def upgrade() -> None:
    password = get_settings().app_db_password.get_secret_value().replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}';
            ELSE
                ALTER ROLE {APP_ROLE} WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}';
            END IF;
        END $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE, INSERT ON alembic_version FROM {APP_ROLE}")

    for table, allow_shared in TENANT_TABLES.items():
        predicate = f"bank_id = {_TENANT}"
        if allow_shared:
            predicate = f"(bank_id IS NULL OR {predicate})"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
