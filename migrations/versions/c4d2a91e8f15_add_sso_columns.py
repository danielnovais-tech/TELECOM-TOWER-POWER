"""add SSO columns to api_keys

Revision ID: c4d2a91e8f15
Revises: a8e7f4d521b6
Create Date: 2026-04-27 17:00:00.000000

Adds OIDC/SSO mapping columns so an api_key row can be reached via an
external IdP (Cognito today; Auth0/Azure AD later). Idempotent guards
keep the migration safe to re-run on environments where the columns
already exist.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d2a91e8f15"
down_revision: Union[str, Sequence[str], None] = "a8e7f4d521b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(insp, table: str) -> set:
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def _indexes(insp, table: str) -> set:
    try:
        return {ix["name"] for ix in insp.get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("api_keys"):
        return
    cols = _columns(insp, "api_keys")
    if "sso_enabled" not in cols:
        op.add_column(
            "api_keys",
            sa.Column("sso_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "oauth_provider" not in cols:
        op.add_column("api_keys", sa.Column("oauth_provider", sa.String(length=20), nullable=True))
    if "oauth_subject" not in cols:
        op.add_column("api_keys", sa.Column("oauth_subject", sa.String(length=255), nullable=True))

    ixs = _indexes(insp, "api_keys")
    if "ix_api_keys_oauth_subject" not in ixs:
        op.create_index(
            "ix_api_keys_oauth_subject",
            "api_keys",
            ["oauth_provider", "oauth_subject"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("api_keys"):
        return
    if "ix_api_keys_oauth_subject" in _indexes(insp, "api_keys"):
        op.drop_index("ix_api_keys_oauth_subject", table_name="api_keys")
    cols = _columns(insp, "api_keys")
    if "oauth_subject" in cols:
        op.drop_column("api_keys", "oauth_subject")
    if "oauth_provider" in cols:
        op.drop_column("api_keys", "oauth_provider")
    if "sso_enabled" in cols:
        op.drop_column("api_keys", "sso_enabled")
