"""
models.py – SQLAlchemy ORM models for Alembic migrations.

These mirror the tables already created by tower_db.py and job_store.py
so that Alembic can manage schema versioning going forward.
"""

from sqlalchemy import Column, Float, Integer, Text
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
