-- ============================================================================
-- Привязка Telegram-чата к объекту ЗПР.
-- ============================================================================
-- Один объект → 0..N чатов (на будущее: чат подрядчика + чат изысканий + …).
-- Один чат → 0..1 объект. Общие чаты (Изыскания ЗПР, новости) остаются с NULL.
-- ============================================================================

alter table tg_chats
    add column object_id uuid references objects(id) on delete set null;

create index tg_chats_object_idx on tg_chats (object_id) where object_id is not null;

insert into _applied_migrations (filename)
values ('20260512000002_tg_chats_object_link.sql')
on conflict do nothing;
