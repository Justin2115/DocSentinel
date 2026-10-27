from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"
if not ENV_FILE.exists():
    # Support the project-root .env used by the repository's local setup while
    # still preferring backend/.env when one is present.
    ENV_FILE = BASE_DIR.parent / ".env"


class Settings(BaseSettings):
    PORT: int = 5000

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "docsentinel_app"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""

    CHROMA_PERSIST_DIR: str = "vector_db/chroma"
    # Smaller multilingual model so indexing can finish locally.
    # bge-m3 is stronger but a multi-GB download that blocked search.

    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    SEMANTIC_TOP_K: int = 10
    # Calibrated on a labeled query set (10 relevant / 12 hard-negative queries)
    # against production chunks with all-MiniLM-L6-v2: worst hard-negative
    # cosine similarity was 0.252, relevant median 0.459. 0.30 gave
    # precision 1.00 / recall 0.80; lower values admitted the 0.25-0.30
    # false-positive band. This is a retrieval relevance score, not an
    # accuracy probability.
    SEMANTIC_MIN_SCORE: float = 0.30

    # PP-Structure table recognition for scanned pages with ruled lines.
    # Benchmarked: 27/27 vs 11/27 cells on English numeric tables, +~10s/page,
    # ~20MB cached models. Disable to revert to text-only scanned pages.
    SCANNED_TABLE_EXTRACTION: bool = True

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

    # Watchdog budget for one document's processing run, in seconds. Normal
    # documents finish well inside 10 minutes; this is deliberately looser so a
    # legitimately slow multi-page scan is never abandoned mid-run. Only
    # documents no worker in this process owns are reported as stalled.
    PROCESSING_TIMEOUT_SECONDS: int = 900

    # Persistent log sink. Background OCR/indexing dies with the server, so
    # stage timings and failures must survive a restart to be diagnosable.
    # Relative to backend/; empty string keeps stderr-only logging.
    LOG_FILE: str = "logs/backend.log"

    # Local Ollama RAG provider. Nothing is sent off this machine: the base URL
    # is expected to be a loopback address, and the model must already be
    # installed (no automatic downloads). Left disabled when OLLAMA_MODEL is
    # empty, so the chatbot stays on deterministic extractive answers.
    #
    # llama3.2:3b is the measured fast local default. Switch it by setting
    # OLLAMA_MODEL to any locally installed model name (ollama list) and restart
    # the backend. Nothing in the code keys off a particular model name.
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"
    OLLAMA_ENABLED: bool = True
    # Reasoning models (qwen3 and similar) answer only after a scratchpad, and
    # that scratchpad counts against OLLAMA_MAX_NEW_TOKENS: with thinking left on
    # the answer field can come back empty and the chatbot silently stays on the
    # extractive answer. Kept off so every model is asked for the same thing - a
    # direct grounded answer. Set true to let a reasoning model think out loud.
    OLLAMA_THINKING: bool = False
    # Connect/keepalive budget for the availability probe, in seconds.
    OLLAMA_CONNECT_TIMEOUT: float = 2.0
    # The measured llama3.2:3b CPU p95 is about 23 seconds; bound slow turns
    # while leaving room for ordinary machine load.
    OLLAMA_GENERATE_TIMEOUT: float = 45.0
    # Restrict the prompt window; the evidence block is trimmed to fit.
    OLLAMA_NUM_CTX: int = 4096
    OLLAMA_MAX_NEW_TOKENS: int = 160

    # Default Admin initialization
    DEFAULT_ADMIN_EMAIL: str = "admin@docsentinel.local"
    DEFAULT_ADMIN_PASSWORD: str = "admin@098"

    @property
    def DATABASE_URL(self) -> URL:
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
