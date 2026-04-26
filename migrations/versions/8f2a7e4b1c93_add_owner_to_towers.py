"""add owner column to towers (OWASP A01 IDOR fix)

Revision ID: 8f2a7e4b1c93
Revises: d92a4c1f7e83
Create Date: 2026-04-26 12:00:00.000000

Adds an ``owner`` column to the ``towers`` table so tenant-created towers
can be scoped to their creator. Pre-existing rows (Anatel/OpenCellID
imports etc.) are backfilled to ``'system'`` and remain readable by all
authenticated callers but cannot be modified by tenants.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f2a7e4b1c93'
down_revision: Union[str, Sequence[str], None] = 'd92a4c1f7e83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("towers"):
        # Nothing to migrate yet — fresh DB, tower_db.py CREATE TABLE will
        # already include the owner column.
        return

    cols = {c["name"] for c in insp.get_columns("towers")}
    if "owner" not in cols:
        op.add_column(
            "towers",
            sa.Column(
                "owner",
                sa.Text(),
                nullable=False,
                server_default="system",
            ),
        )
        op.create_index("ix_towers_owner", "towers", ["owner"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("towers"):
        return
    cols = {c["name"] for c in insp.get_columns("towers")}
    if "owner" in cols:
        try:
            op.drop_index("ix_towers_owner", table_name="towers")
        except Exception:
            pass
        op.drop_column("towers", "owner")
