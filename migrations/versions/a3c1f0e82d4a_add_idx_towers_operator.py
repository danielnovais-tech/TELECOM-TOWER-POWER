"""add idx_towers_operator index

Revision ID: a3c1f0e82d4a
Revises: fd47b6ae46be
Create Date: 2026-04-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3c1f0e82d4a'
down_revision: Union[str, Sequence[str]] = 'fd47b6ae46be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE INDEX IF NOT EXISTS idx_towers_operator ON towers (operator)')


def downgrade() -> None:
    op.drop_index('idx_towers_operator', table_name='towers')
