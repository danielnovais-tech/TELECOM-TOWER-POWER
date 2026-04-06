"""initial_schema

Revision ID: 87200230b1e9
Revises: 
Create Date: 2026-04-05 19:27:51.997099

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '87200230b1e9'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create towers and batch_jobs tables (skip if they already exist)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = inspector.get_table_names()

    if "towers" not in existing:
        op.create_table(
            "towers",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("lat", sa.Float(), nullable=False),
            sa.Column("lon", sa.Float(), nullable=False),
            sa.Column("height_m", sa.Float(), nullable=False),
            sa.Column("operator", sa.Text(), nullable=False),
            sa.Column("bands", sa.Text(), nullable=False),
            sa.Column("power_dbm", sa.Float(), nullable=False, server_default="43.0"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "batch_jobs" not in existing:
        op.create_table(
            "batch_jobs",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tower_id", sa.Text(), nullable=False),
            sa.Column("receivers", sa.Text(), nullable=False),
            sa.Column("result_path", sa.Text()),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    """Drop towers and batch_jobs tables."""
    op.drop_table("batch_jobs")
    op.drop_table("towers")
