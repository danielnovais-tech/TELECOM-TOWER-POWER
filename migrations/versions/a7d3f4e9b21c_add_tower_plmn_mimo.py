# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""add plmn + n_tx_antennas to towers (T20 MOCN/MIMO)

Revision ID: a7d3f4e9b21c
Revises: e9a4f2b81c5d
Create Date: 2026-05-04 23:55:00.000000

T20 — surfaces MOCN attribution and MIMO array geometry on the towers
table so the interference engine can:

* Filter aggressors by ``aggressor_plmn`` (glob: ``"724*"`` for all
  Brazil PLMNs, or exact ``"72411"`` for Vivo).
* Apply the MIMO diversity offset (FSPL/P.1812 path) or configure
  Sionna RT ``PlanarArray`` rows/cols (RT path) without an extra DB
  hop.

Defaults preserve pre-T20 math: ``plmn`` NULL = "unknown operator"
(only matches an absent filter); ``n_tx_antennas`` defaults to 1
(SISO → 0 dB diversity gain).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d3f4e9b21c'
down_revision: Union[str, Sequence[str], None] = 'e9a4f2b81c5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The towers table is created by tower_db.py at first boot via
    # CREATE TABLE IF NOT EXISTS. The migration assumes it exists; if
    # the deploy ran an empty DB and never imported, downgrade is a
    # no-op (idempotent column drops below).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "towers" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("towers")}
    if "plmn" not in cols:
        op.add_column(
            "towers",
            sa.Column("plmn", sa.String(length=6), nullable=True),
        )
        op.create_index("ix_towers_plmn", "towers", ["plmn"])
    if "n_tx_antennas" not in cols:
        op.add_column(
            "towers",
            sa.Column(
                "n_tx_antennas",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "towers" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("towers")}
    if "n_tx_antennas" in cols:
        op.drop_column("towers", "n_tx_antennas")
    if "plmn" in cols:
        # drop_index is idempotent when the index exists
        try:
            op.drop_index("ix_towers_plmn", table_name="towers")
        except Exception:
            pass
        op.drop_column("towers", "plmn")
