from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    PORT: int = 5000

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "docsentinel_app"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""

    CHROMA_PERSIST_DIR: str = "vector_db/chroma"
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    SEMANTIC_TOP_K: int = 10
    RAG_LLM_BASE_URL: str = ""
    RAG_LLM_MODEL: str = ""
    RAG_LLM_API_KEY: str = ""
    RAG_LLM_TIMEOUT_SECONDS: float = 30.0
    RAG_RETRIEVAL_TOP_K: int = 8
    RAG_MIN_SIMILARITY: float = 0.2

    JWT_SECRET: str = "docsentinel-secure-jwt-secret-key-32bytes-or-more"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/auth/google/callback"

    FRONTEND_URL: str = "http://localhost:5173"

    DEFAULT_ADMIN_EMAIL: str = "admin@docsentinel.local"
    DEFAULT_ADMIN_PASSWORD: str = "admin@098"

    OCR_ENGINE: str = "hybrid"
    OCR_LANGUAGE: str = "auto"
    OCR_PAGE_SEG_MODE: int = 6
    OCR_RENDER_DPI: int = 300
    OCR_TESSDATA_PATH: str = ""
    OCR_BLANK_THRESHOLD: float = 0.08
    OCR_MIN_TEXT_PIXELS: int = 150
    OCR_PREPROCESS_DENOISE: bool = True
    OCR_DSK: bool = True
    OCR_ALLOW_VISION_MODEL: bool = False
    OCR_VISION_MODEL: str = "Qwen/Qwen2.5-VL-3B-Instruct"
    OCR_BENCHMARK_PDF: str = ""

    @property
    def DATABASE_URL(self) -> URL:
        if not self.DB_PASSWORD:
            sqlite_path = (BASE_DIR / "docsentinel.db").resolve()
            return URL.create(
                drivername="sqlite",
                database=str(sqlite_path),
            )

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
