"""add api_key column to batch_jobs

Revision ID: b5f8e2a31c7d
Revises: c7d3e1f09a2b
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5f8e2a31c7d'
down_revision: Union[str, Sequence[str]] = 'c7d3e1f09a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute(
            "ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS "
            "api_key TEXT DEFAULT NULL"
        )
    else:
        # SQLite: check if column exists
        result = conn.execute(sa.text("PRAGMA table_info(batch_jobs)"))
        columns = [row[1] for row in result]
        if 'api_key' not in columns:
            op.add_column('batch_jobs', sa.Column('api_key', sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute("ALTER TABLE batch_jobs DROP COLUMN IF EXISTS api_key")
    else:
        # SQLite doesn't support DROP COLUMN in older versions — no-op
        pass
