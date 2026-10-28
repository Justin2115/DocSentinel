import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO)

from app.api.auth import admin_router, router as auth_router
from app.api.documents import router as documents_router
from app.api.query import router as query_router
from app.api.search import router as search_router
from app.api.conversations import router as conversations_router

from app.core.config import BASE_DIR, settings
from app.db.base import Base
import app.models.document
import app.models.embedding
import app.models.user
import app.models.conversation
from app.services.auth_service import init_default_admin


def _configure_file_logging() -> None:
    """Mirror the pipeline logs to a rotating file so they outlive the process.

    OCR and indexing run in daemon threads: when the server stops, anything only
    printed to stderr is lost, which is what made the un-indexed documents
    undiagnosable after the fact.

    The handler goes on the "app" and "docsentinel" logger trees, not the root
    logger. PaddlePaddle replaces the root handlers when it imports, and
    uvicorn's dictConfig does the same at startup, so a root-attached file
    handler stops writing precisely when OCR begins.
    """
    if not settings.LOG_FILE:
        return
    try:
        path = Path(settings.LOG_FILE)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        for name in ("app", "docsentinel"):
            logger_ = logging.getLogger(name)
            if handler not in logger_.handlers:
                logger_.addHandler(handler)
            if logger_.level == logging.NOTSET or logger_.level > logging.INFO:
                logger_.setLevel(logging.INFO)
    except Exception as exc:  # never let logging setup stop the API
        logging.warning("File logging disabled: %s", exc)


_configure_file_logging()

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
app.include_router(query_router)
app.include_router(conversations_router)



def run_startup_recovery() -> dict:
    """Backfill missing embeddings, then retry documents interrupted by a shutdown.

    Returns counts for logging and tests. Both steps read persisted state first
    and only touch work that is actually missing, so a restart never reprocesses
    a document that already has valid output.
    """
    from app.api.documents import requeue_interrupted_documents
    from app.services.embedding_service import reindex_pending_documents
    from app.services.pipeline_metrics import stage

    result: dict[str, int] = {}
    with stage("recovery.backfill_embeddings") as metrics:
        result["backfilled"] = reindex_pending_documents()
        metrics["documents"] = result["backfilled"]
    with stage("recovery.requeue_interrupted") as metrics:
        result["requeued"] = requeue_interrupted_documents()
        metrics["documents"] = result["requeued"]
    return result


@app.on_event("startup")
def initialize_database() -> None:
    import threading

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
                -- Reconcile legacy role values before restoring the full role
                -- constraint. Drop the older three-role constraint first so
                -- department roles can be normalized without violating it.
                ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_supported_role;
                UPDATE users SET role = CASE
                    WHEN UPPER(REPLACE(TRIM(role), ' ', '_')) IN ('SUPER_ADMIN', 'SUPERADMIN', 'SUPER_ADMINISTRATOR') THEN 'SUPER_ADMIN'
                    WHEN UPPER(REPLACE(TRIM(role), ' ', '_')) IN ('ADMIN', 'ADMINISTRATOR') THEN 'ADMIN'
                    WHEN UPPER(REPLACE(TRIM(role), ' ', '_')) IN ('DEPARTMENT_ADMIN', 'DEPT_ADMIN', 'DEPARTMENTADMIN', 'DEPTADMIN') THEN 'DEPARTMENT_ADMIN'
                    WHEN UPPER(REPLACE(TRIM(role), ' ', '_')) IN ('DEPARTMENT_CHECKER', 'DEPT_CHECKER') THEN 'DEPARTMENT_CHECKER'
                    WHEN UPPER(REPLACE(TRIM(role), ' ', '_')) IN ('UPLOAD_CHECKER', 'CHECKER') THEN 'UPLOAD_CHECKER'
                    WHEN UPPER(REPLACE(TRIM(role), ' ', '_')) IN ('DEPARTMENT_MAKER', 'DEPT_MAKER') THEN 'DEPARTMENT_MAKER'
                    ELSE 'UPLOAD_MAKER'
                END;
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_supported_role'
                    ) THEN
                        ALTER TABLE users ADD CONSTRAINT ck_users_supported_role
                        CHECK (role IN (
                            'SUPER_ADMIN', 'DEPARTMENT_ADMIN', 'DEPARTMENT_CHECKER',
                            'DEPARTMENT_MAKER', 'ADMIN', 'UPLOAD_MAKER', 'UPLOAD_CHECKER'
                        ));
                    END IF;
                END $$;
                ALTER TABLE folder_permissions ADD COLUMN IF NOT EXISTS folder_name VARCHAR(100);
                ALTER TABLE folder_permissions ADD COLUMN IF NOT EXISTS role VARCHAR(50);
                ALTER TABLE folder_permissions ADD COLUMN IF NOT EXISTS can_upload BOOLEAN DEFAULT TRUE;
                ALTER TABLE folder_permissions ADD COLUMN IF NOT EXISTS can_review BOOLEAN DEFAULT TRUE;
                ALTER TABLE folder_permissions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
                ALTER TABLE ocr_results ADD COLUMN IF NOT EXISTS raw_text TEXT;
                -- Upgrade conversation tables created by the earlier chat schema.
                -- create_all() creates missing tables but does not add columns to
                -- tables that already exist.
                ALTER TABLE conversations ADD COLUMN IF NOT EXISTS chat_mode VARCHAR(16) NOT NULL DEFAULT 'documents';
                ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS request_metadata JSONB;
                ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64);
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'ck_conversations_chat_mode'
                    ) THEN
                        ALTER TABLE conversations ADD CONSTRAINT ck_conversations_chat_mode
                        CHECK (chat_mode IN ('documents', 'general'));
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_conversation_message_idempotency'
                    ) THEN
                        ALTER TABLE conversation_messages ADD CONSTRAINT uq_conversation_message_idempotency
                        UNIQUE (conversation_id, idempotency_key);
                    END IF;
                END $$;
                CREATE INDEX IF NOT EXISTS ix_conversations_owner_updated
                    ON conversations(owner_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS ix_conversation_messages_order
                    ON conversation_messages(conversation_id, created_at, id);
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_folder_permission_folder_role'
                    ) THEN
                        ALTER TABLE folder_permissions ADD CONSTRAINT uq_folder_permission_folder_role
                        UNIQUE (folder_name, role);
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


    # Recovery for work left behind by a previous shutdown: documents that have
    # OCR text but no embeddings, then documents still stuck in PROCESSING.
    # Sequential in one thread so a backfill and a re-OCR never compete for CPU
    # and torch loads before Paddle (importing torch afterwards fails on Windows).
    threading.Thread(
        target=run_startup_recovery,
        name="docsentinel-startup-recovery",
        daemon=True,
    ).start()
