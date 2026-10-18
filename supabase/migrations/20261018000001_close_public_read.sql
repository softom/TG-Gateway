-- 2026-10-18. Закрыть анонимное чтение реестра.
-- Проверка 2026-10-18 publishable-ключом: tg_messages (≈103 тыс., включая личные чаты),
-- tg_chats, tg_client_chat_access и колонка tg_clients.api_key читались без входа.
-- Все известные потребители (tg_web_admin, ZPR tg_classifier / ui handover, ArchSite через
-- Client API) ходят сервисным ключом или через HTTP API — RLS их не касается.
-- Записи по-прежнему через uploader/admin-политики и service_role.

BEGIN;

DROP POLICY IF EXISTS "tg_chats: anyone can read"     ON tg_chats;
DROP POLICY IF EXISTS "tg_messages: anyone can read"  ON tg_messages;
-- в живой БД политики назывались иначе (проверено 2026-10-18):
DROP POLICY IF EXISTS "tg_chats read"                 ON tg_chats;
DROP POLICY IF EXISTS "tg_messages read"              ON tg_messages;
DROP POLICY IF EXISTS "tg_clients_select"             ON tg_clients;
DROP POLICY IF EXISTS "tg_client_chat_access_select"  ON tg_client_chat_access;

-- Политики чтения для admin не создаём: функции public.user_role() в живой БД нет
-- (проверено 2026-10-18, ошибка 42883). Без SELECT-политики anon/authenticated не читают
-- ничего; service_role обходит RLS.

-- Ключи клиентов не выдаются ролям API вовсе.
REVOKE ALL ON tg_clients, tg_client_chat_access FROM anon, authenticated;

COMMIT;

-- Проверка после применения (publishable-ключом): GET /rest/v1/tg_messages → [] или 401/403,
-- GET /rest/v1/tg_clients?select=api_key → 401/403. Client API /api/v1/status — 200.
