from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    PORT: int = 5000

    DB_HOST: str
    DB_PORT: int = 5432
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str

    CHROMA_PERSIST_DIR: str = "vector_db/chroma"
    # Smaller multilingual model so indexing can finish locally.
    # bge-m3 is stronger but a multi-GB download that blocked search.
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    SEMANTIC_TOP_K: int = 10

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
        extra="ignore"
    )


settings = Settings()
