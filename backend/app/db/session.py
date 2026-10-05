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
    """Release the pooled connection. Session.close() is reusable in SQLAlchemy 2."""
    try:
        db.rollback()
    except Exception:
        pass
    db.expunge_all()
    db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
