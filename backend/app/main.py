import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO)

from app.api.auth import admin_router, router as auth_router
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
app.include_router(admin_router)
app.include_router(documents_router)
app.include_router(search_router)



@app.on_event("startup")
def initialize_database() -> None:
    import threading

    from app.services.embedding_service import reindex_pending_documents

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)

    # Ensure schema has all RBAC & categorization columns without dropping existing data
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_picture VARCHAR(1024);
                ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255);
                ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'UPLOAD_MAKER';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS department VARCHAR(50);
                ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP;
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS department VARCHAR(50);
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS assigned_checker INTEGER REFERENCES users(id) ON DELETE SET NULL;
                ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
                UPDATE users SET role = CASE
                    WHEN UPPER(REPLACE(role, ' ', '_')) IN ('ADMIN', 'ADMINISTRATOR') THEN 'ADMIN'
                    WHEN UPPER(REPLACE(role, ' ', '_')) IN ('UPLOAD_CHECKER', 'CHECKER') THEN 'UPLOAD_CHECKER'
                    ELSE 'UPLOAD_MAKER'
                END;
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_supported_role'
                    ) THEN
                        ALTER TABLE users ADD CONSTRAINT ck_users_supported_role
                        CHECK (role IN ('ADMIN', 'UPLOAD_MAKER', 'UPLOAD_CHECKER'));
                    END IF;
                END $$;
            """))
    except Exception as e:
        logging.warning("Schema compatibility check notice: %s", e)


    # Initialize default admin account and default settings if not already present
    with Session(engine) as session:
        try:
            init_default_admin(session)
        except Exception as e:
            logging.exception("Failed to initialize default admin account: %s", e)

        try:
            from app.services.admin_service import init_system_settings_and_permissions
            init_system_settings_and_permissions(session)
        except Exception as e:
            logging.exception("Failed to initialize default settings and permissions: %s", e)


    # Background embedding reindex thread
    threading.Thread(
        target=reindex_pending_documents,
        name="reindex-pending-embeddings",
        daemon=True,
    ).start()