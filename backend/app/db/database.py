from sqlalchemy import create_engine

from app.core.config import settings

engine = create_engine(
    settings.sqlalchemy_database_url,
    pool_pre_ping=True,
    pool_recycle=300,
)
