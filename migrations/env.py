"""
Alembic environment – supports both SQLite and PostgreSQL.

When the DATABASE_URL environment variable is set the migration targets
PostgreSQL; otherwise it falls back to the SQLite URL from alembic.ini.
"""

import os
from logging.config import fileConfig

import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

from alembic import context

from models import Base  # our declarative base with Tower + BatchJob

# ── Alembic Config object ────────────────────────────────────────
config = context.config

# Python logging from .ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Override URL when DATABASE_URL is set ────────────────────────
database_url = os.getenv("DATABASE_URL")
if database_url:
    # Alembic runs synchronous migrations — swap async drivers for sync ones
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    database_url = database_url.replace("sqlite+aiosqlite://", "sqlite://")
    config.set_main_option("sqlalchemy.url", database_url)

# ── Metadata for autogenerate ────────────────────────────────────
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live connection."""
    import logging
    log = logging.getLogger("alembic.env")
    log.info("Creating engine...")
    engine_kwargs: dict = {
        "prefix": "sqlalchemy.",
        "poolclass": pool.NullPool,
    }
    url = config.get_main_option("sqlalchemy.url") or ""
    if url.startswith("postgresql"):
        engine_kwargs["connect_args"] = {
            "options": "-c lock_timeout=15000 -c statement_timeout=30000",
        }
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        **engine_kwargs,
    )
    log.info("Connecting to database...")
    with connectable.connect() as connection:
        if url.startswith("postgresql"):
            log.info("Connected. Killing stale alembic sessions...")
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "  AND pid <> pg_backend_pid() "
                    "  AND state = 'idle in transaction' "
                    "  AND query LIKE '%%alembic_version%%'"
                )
            )
            connection.commit()
        log.info("Configuring migration context...")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        log.info("Beginning transaction...")
        with context.begin_transaction():
            log.info("Running migrations...")
            context.run_migrations()
            log.info("Migrations complete.")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
