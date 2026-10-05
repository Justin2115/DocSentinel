from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def engine_kwargs() -> dict:
    kwargs: dict = {
        "pool_pre_ping": True,
        "pool_recycle": 180,
    }
    url = settings.sqlalchemy_database_url
    if "postgresql" in str(url):
        kwargs["connect_args"] = {
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        }
    return kwargs


engine = create_engine(
    settings.sqlalchemy_database_url,
    **engine_kwargs(),
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def recycle_connection(db: Session) -> None:
    """Drop the current DBAPI connection so long OCR does not sit on a dead Neon SSL session."""
    try:
        if db.in_transaction():
            db.rollback()
    except Exception:
        pass
    try:
        db.connection().invalidate()
    except Exception:
        pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
