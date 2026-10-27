# Chat conversations and local model

DocSentinel chat supports two modes. **Documents** searches only documents the signed-in user can access and applies the existing evidence and citation checks. **General** sends the question and a short, recent conversation history to the configured local Ollama model without searching documents. Conversation history is stored in PostgreSQL and scoped to the authenticated account.

## Local inference configuration

The backend uses Ollama's local HTTP API. It never downloads a model and does not fall back to a cloud provider. The configured default is `llama3.2:3b`, which is the model installed in the development environment used for this implementation. The requested Qwen3 candidates are not configured by default because they were not installed here for a like-for-like measurement; the evaluator below reports missing candidates without downloading them. Only choose a Qwen tag after installing it and measuring it on the target hardware.

Relevant backend settings (environment variables override these defaults):

| Setting | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama endpoint; keep it local unless the deployment explicitly trusts its network. |
| `OLLAMA_MODEL` | `llama3.2:3b` | Exact locally installed tag. |
| `OLLAMA_ENABLED` | `true` | Enables generation and provider health reporting. |
| `OLLAMA_THINKING` | `false` | Keeps the default turn direct and low-latency. |
| `OLLAMA_CONNECT_TIMEOUT` | `2` seconds | Provider availability probe budget. |
| `OLLAMA_GENERATE_TIMEOUT` | `45` seconds | Generation wall-clock deadline. |
| `OLLAMA_NUM_CTX` | `4096` | Context window passed to Ollama. |
| `OLLAMA_MAX_NEW_TOKENS` | `160` | Document-answer output limit. |
| `OLLAMA_TEMPERATURE` | `0` | Document-answer temperature. |
| `OLLAMA_GENERAL_TEMPERATURE` | `0.2` | General-chat temperature. |

Install a model separately with Ollama, set `OLLAMA_MODEL` to its exact tag, and restart the backend. The authenticated `GET /api/conversations/provider/status` endpoint reports reachability and installed model names; it does not return the configured base URL or credentials. A local model provides local inference, not a guarantee of confidentiality, answer quality, or availability. Secure the Ollama endpoint and host as part of deployment.

## Model smoke evaluation

Run from `backend/`:

```powershell
.\venv\Scripts\python.exe scripts/evaluate_chat_models.py
```

The script queries `/api/tags`, evaluates only installed tags among `llama3.2:3b,qwen3:8b,qwen3:4b`, and never pulls a model. It uses synthetic general, extraction, insufficient-evidence, Hindi, Marathi, and prompt-injection prompts. JSON output includes TTFT, total elapsed time, Ollama-reported output token counts, computed output rate, and simple expected-string checks. These checks are a smoke test, not a quality benchmark. A missing model is reported as missing, not silently substituted. Use `--models` to select exact installed tags and `--max-tokens`/`--num-ctx` to match deployment settings.

Development smoke run on 2026-10-28: Ollama `0.40.11`, `llama3.2:3b`, seven synthetic cases, all seven completed; nine of nine expected-string checks matched. Median TTFT was 54 ms and median total time was 228 ms on the warm local runtime. The general question was 638 ms and the JSON extraction question was 716 ms. These values are from a warmed local run, vary by hardware/model state, and do not compare against Qwen because neither `qwen3:8b` nor `qwen3:4b` was installed. The expected-string count says nothing about overall answer quality.

## Database deployment

New development databases create the conversation tables from SQLAlchemy metadata at application startup. For an existing PostgreSQL installation, apply [20261028_conversations.sql](../database/migrations/20261028_conversations.sql) to create the tables and [20261029_conversation_schema_compat.sql](../database/migrations/20261029_conversation_schema_compat.sql) to upgrade tables created by the initial chat schema. The second migration adds `chat_mode`, request metadata, and idempotency fields that `CREATE TABLE IF NOT EXISTS` cannot add to an existing table. Both scripts are safe to apply repeatedly. Back up the database and verify migrations in staging before production deployment.

The application intentionally uses the repository's existing SQLAlchemy metadata startup convention; it does not have an Alembic migration chain. Keep the SQL migration in sync with `backend/app/models/conversation.py` if the schema changes.

## Conversation API

All routes require the existing bearer-token authentication. Identity comes from the authenticated user dependency; no request-supplied user ID is used for authorization. Ownership checks scope all conversation reads and mutations. An inaccessible or unknown conversation returns 404. Citation metadata and historical document IDs are filtered again against current document-view permissions when history is read.

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/conversations` | List current user's conversations; supports `limit` (1–100) and `before` timestamp. |
| `POST` | `/api/conversations` | Create an owned conversation with `mode` `documents` or `general`. |
| `GET` | `/api/conversations/{id}` | Read a message page; `limit` (1–100), optional `before_id` for older messages. |
| `PATCH` | `/api/conversations/{id}` | Rename, sanitizing whitespace and limiting title length to 120. |
| `DELETE` | `/api/conversations/{id}` | Delete the owned conversation and its messages. |
| `POST` | `/api/conversations/{id}/messages/stream` | Persist the user turn and stream the assistant response. Message max is 8,000 characters; document scope max is 50 IDs. |
| `GET` | `/api/conversations/provider/status` | Return authenticated, non-secret Ollama availability status. |

The stream is fetch-compatible SSE. Events are `accepted`, `status`, `delta`, `complete`, and `error`, each with a JSON `data:` payload. A `complete` event contains the persisted assistant message, citations/sources and engine metadata. A client disconnect signals cancellation, and an in-flight same-conversation turn is rejected with 409. The frontend parser retains incomplete frames between network reads. Request IDs make completed retries replayable without creating a second turn.

Document-mode model text is released in sentence-sized deltas only after the existing evidence, citation, number, and name checks pass. General mode streams Ollama text deltas directly and is explicitly marked as having no document evidence. Failed or interrupted generations are persisted with their status; provider failures do not produce a fabricated answer.

## Operational notes

Generation and retrieval timings are logged without prompt or answer text. Ollama token counters are recorded only when the provider returns them. Conversation history currently supplies up to the eight latest completed messages to generation. Same-conversation concurrent requests are blocked within one backend process; use a shared queue/lock and deployment-level rate limiting before running multiple worker processes or exposing the service to untrusted high-volume traffic. The existing authentication, request-length bounds, ownership enforcement, and document ACL checks remain in force.

## Verification

The conversation tests cover owner isolation, create/rename/delete, general-mode streaming, and request idempotency. The existing RAG and folder-permission suites continue to cover grounded answers and document access boundaries. Run focused checks from `backend/`:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_conversations_api.py tests/test_chat_history.py tests/test_rag_chatbot.py tests/test_folder_permission_enforcement.py -q
```

Build the frontend from `frontend/` with `npm run build`.
