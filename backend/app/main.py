import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO)

from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.core.config import settings
from app.db.base import Base
import app.models.document
import app.models.embedding
import app.models.user
from app.services.auth_service import init_default_admin

app = FastAPI(
    title="DocSentinel API",
    version="1.0.0",
    description="Backend API for the DocSentinel Document Management System",
)

# Configure CORS for frontend access
cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
if settings.FRONTEND_URL and settings.FRONTEND_URL not in cors_origins:
    cors_origins.append(settings.FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(search_router)


@app.on_event("startup")
def initialize_database() -> None:
    import threading

    from app.services.embedding_service import reindex_pending_documents

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)

    # Initialize default admin account if not already present
    with Session(engine) as session:
        try:
            init_default_admin(session)
        except Exception as e:
            logging.exception("Failed to initialize default admin account: %s", e)

    # Background embedding reindex thread
    threading.Thread(
        target=reindex_pending_documents,
        name="reindex-pending-embeddings",
        daemon=True,
    ).start()