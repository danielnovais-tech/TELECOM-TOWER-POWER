# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""add webhooks table

Revision ID: f8b2d1e09a47
Revises: a7d3f4e9b21c
Create Date: 2026-05-05 23:15:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b2d1e09a47"
down_revision: Union[str, Sequence[str], None] = "a7d3f4e9b21c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        # Postgres native array of text; SQLite cannot have ARRAY but the
        # JSON-file backend is used in dev/SQLite, so this table only
        # materialises on Postgres.
        sa.Column("events", sa.dialects.postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("idx_webhooks_enabled", "webhooks", ["enabled"])
    op.create_index(
        "idx_webhooks_events_gin",
        "webhooks",
        ["events"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_webhooks_events_gin", table_name="webhooks")
    op.drop_index("idx_webhooks_enabled", table_name="webhooks")
    op.drop_table("webhooks")
