# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""add audit_log table

Revision ID: a8e7f4d521b6
Revises: f3b9c1d4e7a2
Create Date: 2026-04-27 12:30:00.000000

Append-only audit trail for tenant-scoped privileged actions. SOC 2 /
Enterprise procurement requirement. The table is intentionally simple
(no FK to ``api_keys``) so admins can keep audit rows after a key is
revoked or tenant deleted — exactly the scenario auditors care about.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8e7f4d521b6"
down_revision: Union[str, Sequence[str], None] = "f3b9c1d4e7a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if insp.has_table("audit_log"):
        return
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("actor_email", sa.Text()),
        sa.Column("tier", sa.Text()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text()),
        sa.Column("ip", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("metadata_json", sa.Text()),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_api_key", "audit_log", ["api_key"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    # Composite index for the most common query: "this tenant's recent activity"
    op.create_index(
        "ix_audit_log_api_key_ts",
        "audit_log",
        ["api_key", "ts"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("audit_log"):
        return
    for ix in (
        "ix_audit_log_api_key_ts",
        "ix_audit_log_action",
        "ix_audit_log_api_key",
        "ix_audit_log_ts",
    ):
        try:
            op.drop_index(ix, table_name="audit_log")
        except Exception:
            pass
    op.drop_table("audit_log")
