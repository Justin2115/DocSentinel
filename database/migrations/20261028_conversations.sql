-- Persistent per-account DocSentinel chat history.
-- Safe to run repeatedly; backend startup also creates these tables through
-- SQLAlchemy metadata for new installations.
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(36) PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(120) NOT NULL DEFAULT 'New conversation',
    chat_mode VARCHAR(16) NOT NULL DEFAULT 'documents' CHECK (chat_mode IN ('documents', 'general')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_message_at TIMESTAMPTZ NULL,
    archived_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ix_conversations_owner_user_id
    ON conversations(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_conversations_owner_updated
    ON conversations(owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'completed'
        CHECK (status IN ('completed', 'failed', 'interrupted')),
    model_id VARCHAR(120) NULL,
    usage_metadata JSONB NULL,
    citation_metadata JSONB NULL,
    request_metadata JSONB NULL,
    error_metadata JSONB NULL,
    generation_ms INTEGER NULL CHECK (generation_ms IS NULL OR generation_ms >= 0),
    idempotency_key VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_conversation_message_idempotency
        UNIQUE (conversation_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_conversation_messages_conversation_id
    ON conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS ix_conversation_messages_order
    ON conversation_messages(conversation_id, created_at, id);
