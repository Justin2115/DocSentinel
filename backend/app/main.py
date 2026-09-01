import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine

logging.basicConfig(level=logging.INFO)

from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.core.config import settings
from app.db.base import Base
import app.models.document
import app.models.embedding


app = FastAPI(
    title="DocSentinel API",
    version="1.0.0",
    description="Backend API for the DocSentinel Document Management System",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(documents_router)
app.include_router(search_router)


@app.on_event("startup")
def initialize_database() -> None:
    import threading

    from app.services.embedding_service import reindex_pending_documents

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    threading.Thread(
        target=reindex_pending_documents,
        name="reindex-pending-embeddings",
        daemon=True,
    ).start()