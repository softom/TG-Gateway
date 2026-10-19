# TG-Gateway — контекст для Claude

Сервис-шлюз Telegram → облачный реестр → Client API для клиентов (ЗПР, БД студентов).
Отделён из проекта ЗПР 2026-07-31. Обзор — [README.md](README.md); детали — [docs/architecture.md](docs/architecture.md).
**Рабочая WIKI (Obsidian/Dropbox):** `E:\Dropbox\Приложения\remotely-save\TG-Gateway\WIKI\` (`00_INDEX`, `01_Архитектура`, `02_Client_API`) — актуальная дока ведётся там; `docs/architecture.md` в репо — снапшот.

## Инфраструктура
- **Сервер:** Aeza `77.110.125.137` (Ubuntu 26.04, 1 CPU / 2 GB / 30 GB, Charlotte US).
  ⚠️ **Shared:** на нём же VPN-агента `xray.service` (:443) + `wg-quick@wg0` (:51820) — **НЕ ТРОГАТЬ** `/etc/wireguard`, `/usr/local/etc/xray`, `/root/reality.keys`.
- **Доступ (прямой SSH из Крыма DPI режет — только через jump):**
  ```
  ssh -i C:\Users\tigra\HA\vpnkey root@45.130.42.178   # Beget jump
  → sshpass -p '<AEZA_ROOT_PASS>' ssh root@77.110.125.137
  ```
  Все креды — `C:\Users\tigra\HA\secrets.env` (блоки **Aeza** и **Telegram-admin**). jxdkmf-ключи — в серверном `/opt/zpr/app/config.py`.
- **Облако:** Supabase `jxdkmfidrrwxhyltobnh` (`.supabase.co`). Таблицы: `tg_chats`, `tg_messages`, `tg_clients`, `tg_client_chat_access`.
- **Раскладка сервера:** `/opt/zpr/{app,venv,logs,storage}` (пути исторические). systemd: `zpr-tg-admin`, `zpr-tg-listener`, `zpr-tg-backfill`.

## Как работать
- **Деплой файла:** `ssh -i vpnkey root@45.130.42.178 "sshpass -p '<pass>' ssh -T root@77.110.125.137 'cat > /opt/zpr/app/<file>'" < <local>` → `systemctl restart zpr-tg-admin` (или listener).
- **Boot-цепочка:** `zpr-tg-backfill` (oneshot `--backfill 3`) → `zpr-tg-listener`. Рестарт листенера тянет backfill (Wants) → ~минуты.
- ⚠️ **TG-сессия одна** (`/opt/zpr/.tg_session.session`). Перед `rebackfill`/ручным Telethon — **остановить** `zpr-tg-listener` И `zpr-tg-backfill` + `pkill -f tg_raw_listener.py` (иначе `database is locked`). `/ctl/send-file` — исключение (короткая отправка под `_send_lock`, проверено).
- **Авторизация сессии:** код с Aeza-IP не доставляется → **QR-login** через reverse-proxy на Beget (`qrproxy.py` :8091 → Aeza:8090). Прямой HTTP к Aeza из Крыма недоступен.
- ⚠️ **Обычная Claude-сессия в auto-режиме блокирует SSH на внешний хост** (anti-injection). Нужен режим **acceptEdits**.

## Архив вложений (с 2026-10-19)
- На сервере хранятся вложения за **4 календарных месяца** (текущий + 3). Старше — переносятся в Dropbox-приложение **TELEGRAM_UG** (App folder → `E:\Dropbox\Приложения\TELEGRAM_UG\`), раскладка `/<Диалог> [<abs_chat_id>]/<YYYY-MM>/<msg>_<file>`.
- `archive_media.py` (сверка Dropbox content-hash → только потом удаление с сервера) + манифест `storage/ARCHIVED.tsv` (`archive_manifest.py`). `/api/v1/media` по архивным → **410**; `rebackfill_media.py` их не качает.
- Запуск: `tg-archive.service` (oneshot) ← `tg-archive.timer` (1-го числа 03:00 UTC) и кнопка «💾 Бэкап сейчас» на дашборде. Лог `/opt/zpr/logs/archive.log`.
- Креды Dropbox: `secrets.env` `DROPBOX_ARCHIVE_*`, на сервере `config.py` `DROPBOX_*`. ⚠ Права токена фиксируются при выдаче кода — авторизовать ссылкой с явным `&scope=files.metadata.read files.content.write files.content.read`.
- Причина: с 23.07 по 19.10 медиа не качались вовсе — листенер не качает при свободном месте <5 GB (`DISK_MIN_FREE_GB`), диск 30 GB общий с VPN.

## Client API + клиенты
- Эндпоинты: `/api/v1/{chats,messages,search,media,status}`, `/health`, `POST /ctl/send-file`. См. README.
- **Клиент студентов:** `bf2ed1bd-4d10-4b36-8e29-37c12d07ed01` — 45 чатов (группы Арх/б-22-1-о, Арх/б-22-2-о + 42 DM), read+write. Ключ у пользователя / в БД-сессии.
- **Потребители:** ЗПР-классификатор (Beget, читает реестр → события ЗПР), БД студентов (ArchSite, `api.2vhutemas.ru`).

## Открытые хвосты
- Миграция колонок `media_is_proxy` / `media_original_size` на jxdkmf (нужен DB-пароль/pooler-строка проекта — REST-ключом DDL не сделать). API уже сигнализирует `X-Media-Proxy` (пока всё «оригинал»).
- Автомат сжатия крупных PDF/PNG/TIF → ≤100MB (`compress_media.py`) — спроектирован, отложен (1-CPU/2GB медленно на гигантах; gs через /tmp из-за AppArmor).
- Опц. ротация cloud `service_role`.

## Секреты — правило
Никаких паролей/ключей/токенов в git и в этих доках. Только в `C:\Users\tigra\HA\secrets.env` и в серверном `config.py` (он в `.gitignore`).
