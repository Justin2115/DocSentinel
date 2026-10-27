-- Upgrade existing conversation tables from the initial persistent-chat schema.
-- Safe to apply repeatedly; all columns are additive and new values have defaults
-- or are nullable so existing rows remain valid.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS chat_mode VARCHAR(16) NOT NULL DEFAULT 'documents';

ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS request_metadata JSONB;

ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64);

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_conversations_chat_mode'
    ) THEN
        ALTER TABLE conversations
            ADD CONSTRAINT ck_conversations_chat_mode
            CHECK (chat_mode IN ('documents', 'general'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_conversation_message_idempotency'
    ) THEN
        ALTER TABLE conversation_messages
            ADD CONSTRAINT uq_conversation_message_idempotency
            UNIQUE (conversation_id, idempotency_key);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_conversations_owner_updated
    ON conversations(owner_user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_conversation_messages_order
    ON conversation_messages(conversation_id, created_at, id);
