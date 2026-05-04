# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""add cable_loss_db to link_observations

Revision ID: e9a4f2b81c5d
Revises: c4d2a91e8f15
Create Date: 2026-05-03 21:00:00.000000

Adds the rx-side cable / connector loss (dB) so the trainer can
reconstruct basic transmission loss faithfully from drive-test RSSI:

    Lb = (Pt + Gt + Gr) - (Prx + L_cable)

Default 0.0 keeps the existing (synthetic + legacy `api`) rows
mathematically unchanged. The drivetest validator (commit B) refuses
rows with `source LIKE 'drivetest_%'` that omit it explicitly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9a4f2b81c5d'
down_revision: Union[str, Sequence[str], None] = 'c4d2a91e8f15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The link_observations table is created lazily by observation_store.py
    # in CREATE TABLE IF NOT EXISTS form, so it may not exist yet on a
    # brand-new DB that hasn't received any /coverage/observations call.
    # Guard the ALTER so the migration is idempotent across both states.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "link_observations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("link_observations")}
    if "cable_loss_db" in cols:
        return
    op.add_column(
        "link_observations",
        sa.Column(
            "cable_loss_db",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "link_observations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("link_observations")}
    if "cable_loss_db" not in cols:
        return
    op.drop_column("link_observations", "cable_loss_db")
