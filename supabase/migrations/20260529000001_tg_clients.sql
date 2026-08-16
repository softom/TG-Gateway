-- Миграция: API-клиенты TG-листенера.
-- Применяется на ОБЛАЧНОМ Supabase (jxdkmfidrrwxhyltobnh).
-- Таблицы: tg_clients + tg_client_chat_access.

-- ── tg_clients: внешние приложения с API-ключами ──
CREATE TABLE IF NOT EXISTS tg_clients (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    api_key     text NOT NULL UNIQUE,
    is_active   boolean NOT NULL DEFAULT true,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  tg_clients          IS 'API-клиенты TG-листенера (внешние приложения)';
COMMENT ON COLUMN tg_clients.api_key  IS 'Уникальный ключ вида zpr_... — передаётся в X-Api-Key';
COMMENT ON COLUMN tg_clients.is_active IS 'false = ключ заблокирован, API вернёт 403';

-- ── tg_client_chat_access: per-client разрешения на чаты ──
CREATE TABLE IF NOT EXISTS tg_client_chat_access (
    client_id   uuid   NOT NULL REFERENCES tg_clients(id) ON DELETE CASCADE,
    chat_id     bigint NOT NULL REFERENCES tg_chats(chat_id) ON DELETE CASCADE,
    can_read    boolean NOT NULL DEFAULT true,
    can_write   boolean NOT NULL DEFAULT false,
    PRIMARY KEY (client_id, chat_id)
);

COMMENT ON TABLE tg_client_chat_access IS 'Какие чаты доступны клиенту (read/write)';

-- ── RLS: SELECT open, writes via service_role ──
ALTER TABLE tg_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE tg_client_chat_access ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tg_clients_select"
    ON tg_clients FOR SELECT USING (true);
CREATE POLICY "tg_client_chat_access_select"
    ON tg_client_chat_access FOR SELECT USING (true);

-- ── Trigger: auto-update updated_at ──
CREATE OR REPLACE FUNCTION tg_clients_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tg_clients_updated_at
    BEFORE UPDATE ON tg_clients
    FOR EACH ROW
    EXECUTE FUNCTION tg_clients_touch_updated_at();

-- ── Индексы ──
CREATE INDEX IF NOT EXISTS idx_tg_clients_api_key
    ON tg_clients (api_key) WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_tg_client_chat_access_client
    ON tg_client_chat_access (client_id);
