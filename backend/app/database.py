"""
Database engine and session management.

Usage in a route:
    from app.database import get_db
    def endpoint(db: Session = Depends(get_db)): ...

Usage in a Celery task (no request lifecycle to hang a Depends off of):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        ...
    finally:
        db.close()
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # avoids "server closed the connection unexpectedly" after idle time
    future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


class Base(DeclarativeBase):
    """All ORM models inherit from this."""
    pass


def get_db() -> Generator:
    """FastAPI dependency: yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
