-- ============================================================================
-- Telegram raw-буфер: чаты и сообщения из whitelisted Telegram-групп.
-- Источник — telegram_listener.py (Telethon / MTProto).
-- ============================================================================
-- Назначение: «сырое» хранилище входящих сообщений и их вложений из
-- whitelisted Telegram-чатов. LLM-классификация в documents / letters /
-- tasks выполняется отдельным процессом позднее (handler='raw' пока
-- единственный). Источник истины для распознавания истории чатов.
-- ============================================================================

-- ── tg_chats ──────────────────────────────────────────────────────────────────

create table tg_chats (
    chat_id         bigint primary key,
    title           text,
    username        text,
    kind            text check (kind in ('channel','group','supergroup','user')),
    is_whitelisted  boolean not null default true,
    handler         text not null default 'raw' check (handler in ('raw')),
    note            text,
    first_seen_at   timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

comment on table  tg_chats is
    'Реестр Telegram-чатов, попавших в whitelist. Заполняется telegram_listener.py при первом сообщении из чата + командой --sync-whitelist.';
comment on column tg_chats.chat_id is
    'PK. Telegram chat_id (для групп/каналов отрицательный, для супергрупп с префиксом -100).';
comment on column tg_chats.title is
    'Snapshot имени чата. Обновляется при каждом сообщении (Telethon отдаёт актуальный title в Event.chat).';
comment on column tg_chats.username is
    'Snapshot @username чата (без @). NULL для приватных групп.';
comment on column tg_chats.kind is
    'Тип чата: channel | group | supergroup | user. Определяется из telethon.tl.types.';
comment on column tg_chats.is_whitelisted is
    'true = чат активно слушается. false = выключен (история остаётся, новых сообщений не пишем).';
comment on column tg_chats.handler is
    'Обработчик сообщений. Сейчас единственный: raw (всё в tg_messages без классификации).';

-- ── tg_messages ───────────────────────────────────────────────────────────────

create table tg_messages (
    id                 uuid primary key default gen_random_uuid(),
    chat_id            bigint not null references tg_chats(chat_id) on delete cascade,
    message_id         bigint not null,
    thread_id          bigint,
    reply_to_msg_id    bigint,

    sender_id          bigint,
    sender_username    text,
    sender_name        text,

    msg_date           timestamptz not null,
    edit_date          timestamptz,

    text               text,

    has_media          boolean not null default false,
    media_kind         text check (media_kind in
        ('photo','document','video','audio','voice','sticker','gif',
         'poll','contact','geo','webpage','other')),
    media_path         text,
    media_file_name    text,
    media_mime         text,
    media_size         bigint,

    raw_json           jsonb,

    ingested_at        timestamptz not null default now(),

    unique (chat_id, message_id)
);

comment on table  tg_messages is
    'Сырой буфер Telegram-сообщений (handler=raw). LLM-классификация в documents/letters/tasks — отдельный процесс позднее.';
comment on column tg_messages.chat_id is
    'FK → tg_chats.chat_id (CASCADE). Сообщения уничтожаются при удалении чата из реестра.';
comment on column tg_messages.message_id is
    'Внутренний id сообщения в Telegram (уникален внутри чата). UNIQUE(chat_id, message_id) — защита от дублей при backfill.';
comment on column tg_messages.thread_id is
    'Topic ID для форумов (supergroup с включёнными темами). NULL для обычных чатов.';
comment on column tg_messages.reply_to_msg_id is
    'message_id сообщения, на которое отвечают. NULL если не ответ.';
comment on column tg_messages.sender_id is
    'Telegram user_id (или channel_id для каналов от имени канала). NULL для системных сообщений.';
comment on column tg_messages.sender_username is
    'Snapshot @username отправителя на момент сообщения.';
comment on column tg_messages.sender_name is
    'Snapshot "Имя Фамилия" отправителя на момент сообщения.';
comment on column tg_messages.msg_date is
    'Дата сообщения в Telegram (UTC).';
comment on column tg_messages.edit_date is
    'Дата последней правки сообщения. NULL если не редактировалось.';
comment on column tg_messages.text is
    'Текст сообщения или caption для медиа. NULL для медиа без подписи.';
comment on column tg_messages.has_media is
    'true если у сообщения есть прикреплённый файл/медиа.';
comment on column tg_messages.media_kind is
    'Тип медиа: photo | document | video | audio | voice | sticker | gif | poll | contact | geo | webpage | other.';
comment on column tg_messages.media_path is
    'Относительный путь файла в STORAGE_DIR. Пример: TELEGRAM/2026/05/1234567890/42_контракт.pdf. NULL если медиа не скачивалось.';
comment on column tg_messages.media_file_name is
    'Оригинальное имя файла (из Telethon Document.attributes), как было задано отправителем.';
comment on column tg_messages.raw_json is
    'Сырое представление telethon.tl.types.Message в JSON (через .to_json()). Для отладки, forward-info, поля-расширения.';
comment on column tg_messages.ingested_at is
    'Когда сообщение сохранили в БД (не равно msg_date — отличается при backfill).';

-- ── Индексы ───────────────────────────────────────────────────────────────────

create index tg_messages_chat_idx   on tg_messages (chat_id, msg_date desc);
create index tg_messages_sender_idx on tg_messages (sender_id);
create index tg_messages_date_idx   on tg_messages (msg_date desc);
create index tg_messages_media_idx  on tg_messages (has_media) where has_media = true;

-- ── updated_at-триггер для tg_chats ───────────────────────────────────────────

create or replace function tg_chats_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger tg_chats_updated_at_trg
    before update on tg_chats
    for each row execute function tg_chats_updated_at();

-- ── RLS ───────────────────────────────────────────────────────────────────────
-- Та же модель, что у letters/documents:
-- viewer/anon — SELECT; uploader — +INSERT/UPDATE; admin — +DELETE; service_role — всё.

alter table tg_chats    enable row level security;
alter table tg_messages enable row level security;

create policy "tg_chats: anyone can read"        on tg_chats    for select using (true);
create policy "tg_chats: uploader can write"     on tg_chats    for insert with check (public.user_role() in ('uploader','admin'));
create policy "tg_chats: uploader can update"    on tg_chats    for update using       (public.user_role() in ('uploader','admin'));
create policy "tg_chats: admin can delete"       on tg_chats    for delete using       (public.user_role() = 'admin');

create policy "tg_messages: anyone can read"     on tg_messages for select using (true);
create policy "tg_messages: uploader can insert" on tg_messages for insert with check (public.user_role() in ('uploader','admin'));
create policy "tg_messages: uploader can update" on tg_messages for update using       (public.user_role() in ('uploader','admin'));
create policy "tg_messages: admin can delete"    on tg_messages for delete using       (public.user_role() = 'admin');

-- ── Регистрация в реестре миграций ───────────────────────────────────────────

insert into _applied_migrations (filename) values ('20260512000001_tg_messages.sql')
on conflict do nothing;
