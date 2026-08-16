# TG-Gateway

Сервис-шлюз между **Telegram** и внутренними клиентами. Собирает приватные
Telegram-чаты личного аккаунта в облачный реестр и отдаёт их клиентам
(проект ЗПР, БД студентов и др.) через **Client API** (чаты, сообщения, поиск,
байты вложений, публикация файлов).

> Вырос из TG-подсистемы проекта ЗПР и **отделён в самостоятельный проект
> 2026-07-31**. ЗПР теперь — один из клиентов (наравне со студенческой БД).

## Архитектура

```
Telegram ──MTProto──> [Aeza US: tg_raw_listener (systemd)] ──REST──> Supabase Cloud
                        + web-админка :8080  + Client API              (tg_chats, tg_messages,
                        + rebackfill / send-file                        tg_clients, *_access)
                                                                             │
   Клиенты (по X-Api-Key / admin_token):                                     ▼
   • ЗПР-классификатор (Beget) — читает реестр → строит события ЗПР
   • БД студентов (ArchSite)   — тянет сообщения/файлы/поиск
```

- **Сбор** идёт из-за рубежа (Telegram режет MTProto из РФ). Сервер — **Aeza (Charlotte, US)**.
- **Облако** = тонкий реестр (сырьё + клиенты доступа). Никакой бизнес-логики клиентов.
- **Клиенты** сами решают, что делать с данными (ЗПР → события; студенты → ведомости).

## Компоненты

| Файл | Роль |
|---|---|
| `tg_raw_listener.py` | демон приёма (`--listen`) + дозагрузка (`--backfill N`) + `--auth` |
| `tg_web_admin.py` | FastAPI: дашборд, CRUD клиентов, доступы, **Client API**, `/ctl/send-file` |
| `rebackfill_media.py` | дотяжка пропавших медиа из Telegram (по клиенту или точечно) |
| `scripts/send_telegram_protocol.py` | референс-скрипт рассылки .docx (логика для `/ctl/send-file`) |
| `migrate_chat_mapping.py` | перенос привязок чат→объект |
| `tg_web/` | шаблоны + CSS + systemd-юнит админки |
| `supabase/migrations/` | схема реестра (`tg_messages`, `tg_chats.object_id`, `tg_clients`) |

## Client API (для внешних приложений)

Авторизация: `X-Api-Key: zpr_...` (или `?api_key=`). Доступ — по `tg_client_chat_access`.

| Метод | Эндпоинт | Назначение |
|---|---|---|
| GET | `/api/v1/chats` | доступные клиенту чаты |
| GET | `/api/v1/messages?chat_id=&since=&limit=&offset=` | сообщения |
| GET | `/api/v1/search?q=&sender=&chat_id=&…` | поиск по тексту+имени файла и/или отправителю |
| GET | `/api/v1/media?chat_id=&message_id=` | байты вложения (заголовок `X-Media-Proxy`) |
| GET | `/api/v1/status` | счётчики |
| GET | `/health` | healthcheck |
| POST | `/ctl/send-file` | публикация файла в чаты (multipart; `admin_token`; 403 при плохом токене) |

## Сервер и деплой

- **Aeza** `77.110.125.137` (Ubuntu 26.04, 1CPU/2GB). Доступ только через **jump-host Beget** (DPI из Крыма блэкхолит Aeza): `ssh -i vpnkey root@45.130.42.178` → `sshpass` на Aeza. Пароли/ключи — в `secrets.env` (блоки Aeza / TG-admin).
- Раскладка: `/opt/zpr/{app,venv,logs,storage}` (пути исторические от эпохи ЗПР).
- **systemd:** `zpr-tg-admin.service` (:8080), `zpr-tg-listener.service` (`--listen`), `zpr-tg-backfill.service` (oneshot). Boot-цепочка: `network-online → backfill → listener` (общая TG-сессия → строго последовательно).
- Деплой файла: `ssh … 'cat > /opt/zpr/app/<file>'` + `systemctl restart …`.

## Подводные камни

- **TG-сессия** одна (`/opt/zpr/.tg_session.session`). Нельзя запускать 2 клиента разом (SQLite-lock / AuthKeyDuplicated). Копировать между машинами — нельзя. `/ctl/send-file` — короткая отправка под lock, проверено ок.
- **Авторизация** новой сессии: код с дата-центрового Aeza-IP Telegram не доставляет → **QR-login** через reverse-proxy на Beget.
- **Медиа `media_path`** заполнен не у всех записей → `/api/v1/media` имеет glob-fallback; критерий «пробела» — отсутствие файла, не `NULL`.
- **AppArmor** профиль `/etc/apparmor.d/gs` запрещает `gs` доступ к `/opt/zpr/storage` (для сжатия PDF работать через /tmp).
- **`config.py`** и `.tg_session*` — в `.gitignore`.

Полная история/детали — `docs/architecture.md`.
