"""add spatial gist index on tower coordinates

Revision ID: e4a2b8c61d3f
Revises: c7d3e1f09a2b
Create Date: 2026-04-14 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e4a2b8c61d3f'
down_revision: Union[str, Sequence[str]] = 'c7d3e1f09a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        # Cannot use CONCURRENTLY inside a transaction; use non-concurrent create
        op.execute(
            'CREATE INDEX IF NOT EXISTS idx_towers_coords '
            'ON towers USING gist(ll_to_earth(lat, lon))'
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS idx_towers_coords')
