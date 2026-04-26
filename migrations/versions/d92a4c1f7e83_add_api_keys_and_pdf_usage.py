"""add api_keys and pdf_usage_monthly tables

Revision ID: d92a4c1f7e83
Revises: 10b0a7c87e25
Create Date: 2026-04-26 00:00:00.000000

Persists API keys and monthly PDF quota counters in PostgreSQL,
replacing the in-memory + key_store.json hybrid.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd92a4c1f7e83'
down_revision: Union[str, Sequence[str], None] = '10b0a7c87e25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if not insp.has_table("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("api_key", sa.Text(), primary_key=True),
            sa.Column("tier", sa.Text(), nullable=False),
            sa.Column("owner", sa.Text(), nullable=False),
            sa.Column("email", sa.Text(), nullable=False),
            sa.Column("stripe_customer_id", sa.Text(), nullable=True),
            sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
            sa.Column("billing_cycle", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_api_keys_email", "api_keys", ["email"])

    if not insp.has_table("pdf_usage_monthly"):
        op.create_table(
            "pdf_usage_monthly",
            sa.Column("api_key", sa.Text(), nullable=False),
            sa.Column("period", sa.Text(), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("api_key", "period", name="pk_pdf_usage_monthly"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if insp.has_table("pdf_usage_monthly"):
        op.drop_table("pdf_usage_monthly")
    if insp.has_table("api_keys"):
        op.drop_index("ix_api_keys_email", table_name="api_keys")
        op.drop_table("api_keys")
