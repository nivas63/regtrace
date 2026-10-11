"""Treat NULL bank_id as equal in graph/mapping uniqueness (public nodes).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE kg_edges DROP CONSTRAINT uq_kg_edges_src_type")
    op.execute(
        "ALTER TABLE kg_edges ADD CONSTRAINT uq_kg_edges_src_type UNIQUE NULLS NOT DISTINCT "
        "(src_type, src_id, rel, dst_type, dst_id, bank_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE kg_edges DROP CONSTRAINT uq_kg_edges_src_type")
    op.execute(
        "ALTER TABLE kg_edges ADD CONSTRAINT uq_kg_edges_src_type UNIQUE "
        "(src_type, src_id, rel, dst_type, dst_id, bank_id)"
    )
