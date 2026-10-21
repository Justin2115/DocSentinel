"""Test configuration with hard isolation from the production application database.

The suite must NEVER connect to the real PostgreSQL instance or the production
Chroma directory. These fixtures rebind the application session factory to a
throwaway SQLite file created under the system temp directory before any test
module can import app code.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Fail fast rather than touch production if isolation is ever lost.
os_env_host = "docsentinel-tests-isolated.invalid"

_TEST_DIR = Path(tempfile.mkdtemp(prefix="docsentinel_pytest_"))
_TEST_DB_PATH = _TEST_DIR / "test_app.db"


def _remove_test_dir() -> None:
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


atexit.register(_remove_test_dir)

# Env override keeps settings.DATABASE_URL pointed at an unreachable host as a
# second guard, then we rebind the session factory to the temp SQLite file.
os.environ["DB_HOST"] = os_env_host
# Stage timings and failures are logged to a rotating file; default it to the
# throwaway directory so test runs do not pollute the server's log.
os.environ.setdefault("LOG_FILE", str(_TEST_DIR / "backend.log"))

import app.db.session as db_session
import app.main  # noqa: F401  (registers all models and routers on Base.metadata)
from app.db.base import Base

_test_engine = create_engine(
    f"sqlite:///{_TEST_DB_PATH}",
    connect_args={"check_same_thread": False},
)
_TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=_test_engine,
)

# Rebind the application session factory to the isolated test database.
db_session.engine = _test_engine
db_session.SessionLocal = _TestSessionLocal

# The FastAPI startup handler creates its own engine from settings; pin it to
# the test engine so lifespan code also stays off the real database.
app.main.create_engine = lambda *args, **kwargs: _test_engine

Base.metadata.create_all(_test_engine)


@pytest.fixture(autouse=True)
def clean_document_tables():
    """Wipe document rows in the ISOLATED test DB so tests start from a clean slate."""
    from app.models.document import (
        Document,
        DocumentPage,
        ExtractedField,
        ExtractedTable,
        OCRResult,
        ReviewQueue,
    )
    from app.models.embedding import EmbeddingIndex

    db = _TestSessionLocal()
    try:
        db.query(ReviewQueue).delete()
        db.query(EmbeddingIndex).delete()
        db.query(ExtractedTable).delete()
        db.query(ExtractedField).delete()
        db.query(OCRResult).delete()
        db.query(DocumentPage).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def fake_embeddings(tmp_path):
    from app.services.embedding_service import (
        reset_chroma_client,
        set_encode_override,
    )
    from tests.helpers import use_test_embeddings

    use_test_embeddings(tmp_path)
    yield
    set_encode_override(None)
    reset_chroma_client()
