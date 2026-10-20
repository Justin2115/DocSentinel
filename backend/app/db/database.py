import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "docsentinel")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD") or ""

if not DB_PASSWORD:
    DATABASE_URL = "sqlite:///" + os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docsentinel.db"))
else:
    DATABASE_URL = (
        f"postgresql+psycopg2://"
        f"{DB_USER}:{DB_PASSWORD}@"
        f"{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)