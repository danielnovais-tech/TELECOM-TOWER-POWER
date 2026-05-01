# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""add branding column to api_keys

Revision ID: f3b9c1d4e7a2
Revises: 8f2a7e4b1c93
Create Date: 2026-04-26 23:30:00.000000

White-label support for Enterprise tenants. The column stores a
JSON blob with arbitrary branding data (logo_url, primary_color,
secondary_color, company_name, frontend_url, …). Pre-existing
rows get NULL — the app treats NULL as "no branding overrides,
use the platform defaults".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b9c1d4e7a2'
down_revision: Union[str, Sequence[str], None] = '8f2a7e4b1c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("api_keys"):
        # No-op for environments that haven't run d92a4c1f7e83 yet.
        return
    cols = {c["name"] for c in insp.get_columns("api_keys")}
    if "branding" not in cols:
        # Use Text + JSON encode/decode at the app layer to stay portable
        # across SQLite (used in CI) and PostgreSQL (prod).
        op.add_column("api_keys", sa.Column("branding", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("api_keys"):
        return
    cols = {c["name"] for c in insp.get_columns("api_keys")}
    if "branding" in cols:
        op.drop_column("api_keys", "branding")
