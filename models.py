"""
models.py – SQLAlchemy ORM models for Alembic migrations.

These mirror the tables already created by tower_db.py and job_store.py
so that Alembic can manage schema versioning going forward.
"""

from sqlalchemy import Column, Float, Integer, PrimaryKeyConstraint, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Tower(Base):
    __tablename__ = "towers"

    id = Column(Text, primary_key=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    height_m = Column(Float, nullable=False)
    operator = Column(Text, nullable=False)
    bands = Column(Text, nullable=False)
    power_dbm = Column(Float, nullable=False, server_default="43.0")
    # OWASP A01: per-row tenant scope. Pre-existing imports (Anatel,
    # OpenCellID, …) are owned by "system" and are read-only for tenants.
    owner = Column(Text, nullable=False, server_default="system", index=True)


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id = Column(Text, primary_key=True)
    status = Column(Text, nullable=False, server_default="queued")
    progress = Column(Integer, nullable=False, server_default="0")
    total = Column(Integer, nullable=False, server_default="0")
    tower_id = Column(Text, nullable=False)
    receivers = Column(Text, nullable=False)
    result_path = Column(Text)
    error = Column(Text)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)


class ApiKey(Base):
    """Persistent API-key store (replaces key_store.json)."""

    __tablename__ = "api_keys"

    api_key = Column(Text, primary_key=True)
    tier = Column(Text, nullable=False)
    owner = Column(Text, nullable=False)
    email = Column(Text, nullable=False, index=True)
    stripe_customer_id = Column(Text)
    stripe_subscription_id = Column(Text)
    billing_cycle = Column(Text)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)
    # JSON blob: white-label/tenant branding (Enterprise). Stored as Text
    # (SQLite-compatible) and decoded by key_store_db.
    branding = Column(Text)


class PdfUsageMonthly(Base):
    """Per-(api_key, YYYY-MM) PDF export counter for monthly quota enforcement."""

    __tablename__ = "pdf_usage_monthly"

    api_key = Column(Text, nullable=False)
    period = Column(Text, nullable=False)  # YYYY-MM (UTC)
    count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        PrimaryKeyConstraint("api_key", "period", name="pk_pdf_usage_monthly"),
    )


class AuditLog(Base):
    """Append-only audit trail of tenant-scoped privileged actions.

    SOC 2 / Enterprise procurement requirement. Every mutation that
    touches tenant-owned state (branding changes, batch jobs, key
    issuance, tier changes) appends one row here. Reads are scoped to
    the calling tenant via ``api_key``; admins (``owner='system'``) can
    read across tenants.

    Stored as Text + JSON to stay portable across SQLite (CI) and
    PostgreSQL (prod). ``ts`` is a Float (epoch seconds) — same shape as
    the rest of the schema.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(Float, nullable=False, index=True)
    api_key = Column(Text, nullable=False, index=True)
    actor_email = Column(Text)
    tier = Column(Text)
    action = Column(Text, nullable=False, index=True)
    target = Column(Text)
    ip = Column(Text)
    user_agent = Column(Text)
    metadata_json = Column(Text)  # arbitrary JSON blob, ≤ 4 KB after dump
