from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
_root_env = REPO_ROOT / ".env"
_backend_env = BASE_DIR / ".env"
ENV_FILE = _root_env if _root_env.exists() else _backend_env


def _to_psycopg2_url(dsn: str) -> str:
    raw = dsn.strip().strip('"').strip("'")
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://") and "+psycopg2" not in raw:
        raw = "postgresql+psycopg2://" + raw[len("postgresql://") :]

    parts = urlsplit(raw)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "channel_binding"
    ]
    query_keys = {key.lower() for key, _ in query}
    if "sslmode" not in query_keys:
        query.append(("sslmode", "require"))
    return urlunsplit(parts._replace(query=urlencode(query)))


class Settings(BaseSettings):
    PORT: int = 5000

    # Neon / any hosted Postgres: full DSN wins over DB_* parts.
    DATABASE_URL: str | None = None
    DATABASE_URL_POOLED: str | None = None

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "docsentinel_app"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""

    CHROMA_PERSIST_DIR: str = "vector_db/chroma"
    # Smaller multilingual model so indexing can finish locally.
    # bge-m3 is stronger but a multi-GB download that blocked search.
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    SEMANTIC_TOP_K: int = 10

    # Authentication & Security
    JWT_SECRET: str = "docsentinel-secure-jwt-secret-key-32bytes-or-more"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/auth/google/callback"

    # Frontend URL for OAuth redirects & CORS
    FRONTEND_URL: str = "http://localhost:5173"

    # Default Admin initialization
    DEFAULT_ADMIN_EMAIL: str = "admin@docsentinel.local"
    DEFAULT_ADMIN_PASSWORD: str = "admin@098"

    @property
    def sqlalchemy_database_url(self) -> str | URL:
        dsn = (self.DATABASE_URL or self.DATABASE_URL_POOLED or "").strip()
        if dsn:
            return _to_psycopg2_url(dsn)
        return URL.create(
            drivername="postgresql+psycopg2",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else ".env",
        extra="ignore",
    )


settings = Settings()
