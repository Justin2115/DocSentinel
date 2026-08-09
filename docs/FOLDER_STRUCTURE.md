# DocSentinel — Folder Structure

```
DocSentinel/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                     # FastAPI application entry point
│   │   │
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py                 # Authentication endpoints
│   │   │   ├── documents.py            # Document upload/search APIs
│   │   │   └── query.py                # RAG & AI query endpoints
│   │   │
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── upload_service.py       # Upload & storage logic
│   │   │   ├── ocr_service.py          # OCR orchestration (later)
│   │   │   ├── extraction_service.py   # Module 2 integration (later)
│   │   │   ├── embedding_service.py    # Module 1 integration (later)
│   │   │   └── workflow_service.py     # Workflow management
│   │   │
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── role.py
│   │   │   ├── folder.py
│   │   │   ├── document.py
│   │   │   ├── workflow_status.py
│   │   │   ├── audit.py
│   │   │   └── processing_job.py
│   │   │
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── auth.py
│   │   │   ├── folder.py
│   │   │   ├── document.py
│   │   │   ├── query.py
│   │   │   └── workflow.py
│   │   │
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   └── dependencies.py
│   │   │
│   │   └── db/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── session.py
│   │      
│   │
│   ├── tests/
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .gitignore
│
├── frontend/
│   ├── src/
│   │   ├── api/                        # Frontend API wrappers
│   │   ├── assets/                     # Static assets
│   │   ├── components/                 # Reusable React components
│   │   ├── hooks/                      # Custom React hooks
│   │   ├── pages/                      # Application pages
│   │   └── types/                      # Shared TypeScript types
│   │
│   ├── public/
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   └── Dockerfile
│
├── database/
│   ├── migrations/                     # Alembic migrations (future)
│   └── schema.sql                      # Database schema reference
│
├── docker/
│   ├── docker-compose.yml              # Runs all project services together
│   ├── backend.Dockerfile              # Backend Docker image
│   └── frontend.Dockerfile             # Frontend Docker image
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TECH_STACK.md
│   ├── FOLDER_STRUCTURE.md
│   ├── PROJECT_ARCHITECTURE_OVERVIEW.md
│   ├── DATABASE_SCHEMA.md
│   ├── Backend_integration.md
│   └── ingestion_contract.md
│
├── ingestion/                          # File parsers (PDF, DOCX, XLSX, Images)
├── ocr/                                # OCR processing module
├── scripts/                            # Utility scripts
├── storage/                            # Uploaded documents (gitignored)
├── vector_db/                          # Chroma persistence (gitignored)
│
├── .env.example                        # Example environment variables
├── .gitignore                          # Root Git ignore rules
└── README.md
```

## Purpose of key files

- **`backend/app/services/`** — this is where the actual business logic lives (upload validation, OCR invocation, chunking, embedding, RAG orchestration). Keeping this separate from `api/` (routing) follows a service-layer architecture: routes stay thin, logic stays testable in isolation.
- **`backend/app/core/security.py`** (inside `core/`) — JWT creation/validation and password hashing live here, isolated so authentication logic isn't duplicated across routes.
- **`ingestion/`** — one parser module per file type, each conforming to the shared ingestion contract (see `ingestion-contract.md`). This is what lets Person B and C's code stay format-agnostic.
- **`docker/docker-compose.yml`** — single source of truth for how all services (backend, frontend, Postgres, Chroma, MinIO) are wired together; this is what a teammate or judge runs to get the whole system up.
- **`.env.example`** — lists every environment variable the system needs (DB URL, JWT secret, Chroma path, MinIO credentials) without committing actual secrets — real `.env` is gitignored.
- **`storage/` and `vector_db/`** — both gitignored; they hold runtime data, not source code, and should never be committed.
