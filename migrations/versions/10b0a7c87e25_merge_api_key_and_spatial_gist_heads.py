"""merge api_key and spatial_gist heads

Revision ID: 10b0a7c87e25
Revises: b5f8e2a31c7d, e4a2b8c61d3f
Create Date: 2026-04-17 16:49:10.291232

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '10b0a7c87e25'
down_revision: Union[str, Sequence[str], None] = ('b5f8e2a31c7d', 'e4a2b8c61d3f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
