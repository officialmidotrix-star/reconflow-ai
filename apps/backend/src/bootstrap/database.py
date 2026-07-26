"""
Database setup. One engine, one session factory, one per-request session
dependency - the standard FastAPI pattern. create_all_tables() only works
correctly if every module's models have already been imported somewhere
(so their tables are registered on Base.metadata) - wiring.py guarantees
that by importing every module's router, which transitively imports every
module's models, before this ever runs.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.base import Base

from .config import get_database_url

_database_url = get_database_url()
_connect_args = {"check_same_thread": False} if _database_url.startswith("sqlite") else {}

engine = create_engine(_database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all_tables() -> None:
    Base.metadata.create_all(engine)
