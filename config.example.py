"""config.example.py — конфигурация TG-Gateway.

Скопируйте в config.py и заполните. Все модули (tg_raw_listener / tg_web_admin /
rebackfill_media / scripts/send_telegram_protocol) импортируют значения отсюда.
config.py в .gitignore — секреты в git не уходят.
"""
from pathlib import Path

# ─── Облачный Supabase (реестр Telegram) ─────────────────────────────────────
# Проект-реестр (сейчас `jxdkmfidrrwxhyltobnh`). Только tg_chats / tg_messages /
# tg_clients / tg_client_chat_access. Ключи — Supabase Dashboard → Settings → API.
SUPABASE_URL        = "https://<ref>.supabase.co"
SUPABASE_SECRET_KEY = ""   # service_role (REST). НИКОГДА не в git.

# ─── Telegram (личный аккаунт, MTProto через Telethon) ───────────────────────
# api_id / api_hash — один раз на https://my.telegram.org/apps
TG_API_ID    = 0
TG_API_HASH  = ""
TG_SESSION_PATH = Path("/opt/zpr/.tg_session")            # сессия listener/rebackfill/send
# TG_SESSION_SEND_PATH = Path("/opt/zpr/.tg_session_send")  # опц. отдельная send-сессия (если SQLite-lock)

# ─── Хранилище медиа ─────────────────────────────────────────────────────────
STORAGE_DIR = Path("/opt/zpr/storage")   # TELEGRAM/<ГГГГ>/<ММ>/<abs_chat_id>/<msg_id>_<имя>

# ADMIN_TOKEN у web-админки берётся из окружения (systemd `Environment=ADMIN_TOKEN`),
# а НЕ отсюда — см. deploy.

# Медиа: порог свободного места (ниже — только метаданные) и чаты без скачивания медиа.
DISK_MIN_FREE_GB = 3.0
MEDIA_SKIP_CHATS = [-1001587432709]   # ЧАТ How2AI

# Dropbox-архив вложений (archive_media.py) — креды в secrets.env DROPBOX_ARCHIVE_*
# DROPBOX_APP_KEY = "..."
# DROPBOX_APP_SECRET = "..."
# DROPBOX_REFRESH_TOKEN = "..."
