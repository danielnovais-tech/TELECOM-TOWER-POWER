# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""add cube and earthdistance extensions for deduplication

Revision ID: c7d3e1f09a2b
Revises: a3c1f0e82d4a
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7d3e1f09a2b'
down_revision: Union[str, Sequence[str]] = 'a3c1f0e82d4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS cube')
        op.execute('CREATE EXTENSION IF NOT EXISTS earthdistance')


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('DROP EXTENSION IF EXISTS earthdistance')
        op.execute('DROP EXTENSION IF EXISTS cube')
