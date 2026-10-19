"""rebackfill_media.py — дотяжка недостающих медиафайлов из Telegram.

Зачем: при миграции сервера 27-28.06.2026 потеряно хранилище медиа; в облаке
остались метаданные, но файлов на новом сервере нет. Этот скрипт заново качает
файлы из Telegram и проставляет media_path/file_name/mime/size в tg_messages.

Критерий «нужно качать» = ФАЙЛА НЕТ на диске (по media_path ИЛИ glob), а НЕ
«media_path IS NULL» — потому что у части строк media_path указывает на файлы
старого (удалённого) сервера.

Использование (TG-сессию держит листенер → перед запуском остановить
zpr-tg-listener и zpr-tg-backfill + pkill -f tg_raw_listener.py):
    python rebackfill_media.py <chat>:<msg> <chat>:<msg> ...   # точечно
    python rebackfill_media.py --client <client_id>           # все пробелы клиента
    python rebackfill_media.py --since 2026-07-01 [--exclude <chat_id>,...]
                                                              # все чаты с даты; мелкие файлы первыми
Файлы, перенесённые в Dropbox-архив (ARCHIVED.tsv), не качаются.
"""
import asyncio
import sys
from datetime import timezone
from pathlib import Path

from config import (TG_API_ID, TG_API_HASH, TG_SESSION_PATH, STORAGE_DIR,
                    SUPABASE_URL, SUPABASE_SECRET_KEY)
from telethon import TelegramClient
from supabase import create_client
from tg_raw_listener import (_abs_chat_id, _sanitize, _default_filename,
                             _detect_media, TELEGRAM_STORAGE_ROOT, _DOWNLOAD_KINDS)
from archive_manifest import archived

sb = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
BASE = Path(STORAGE_DIR).resolve()


def _has_file(chat_id: int, message_id: int, media_path: str | None) -> bool:
    if archived(_abs_chat_id(chat_id), message_id):  # уже в Dropbox-архиве — не тянуть обратно
        return True
    if media_path and (BASE / media_path).is_file():
        return True
    for c in BASE.glob(f"TELEGRAM/*/*/{_abs_chat_id(chat_id)}/{message_id}_*"):
        if c.is_file():
            return True
    return False


def _client_targets(client_id: str) -> list[tuple[int, int]]:
    allowed = [a["chat_id"] for a in sb.table("tg_client_chat_access")
               .select("chat_id").eq("client_id", client_id).execute().data]
    media, s = [], 0
    while True:
        ch = (sb.table("tg_messages")
              .select("chat_id,message_id,media_kind,media_path")
              .eq("has_media", True).in_("chat_id", allowed)
              .range(s, s + 999).execute().data or [])
        media += ch
        if len(ch) < 1000:
            break
        s += 1000
    return [(m["chat_id"], m["message_id"]) for m in media
            if m["media_kind"] in _DOWNLOAD_KINDS
            and not _has_file(m["chat_id"], m["message_id"], m["media_path"])]


def _since_targets(since: str, exclude: set[int]) -> list[tuple[int, int]]:
    media, s = [], 0
    while True:
        ch = (sb.table("tg_messages")
              .select("chat_id,message_id,media_kind,media_path,media_size")
              .eq("has_media", True).gte("msg_date", since)
              .range(s, s + 999).execute().data or [])
        media += ch
        if len(ch) < 1000:
            break
        s += 1000
    todo = [m for m in media
            if m["media_kind"] in _DOWNLOAD_KINDS and m["chat_id"] not in exclude
            and not _has_file(m["chat_id"], m["message_id"], m["media_path"])]
    todo.sort(key=lambda m: m.get("media_size") or 0)  # мелкие первыми: максимум файлов до упора в место
    return [(m["chat_id"], m["message_id"]) for m in todo]


async def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--client":
        targets = _client_targets(args[1])
    elif args and args[0] == "--since":
        exclude = set()
        if "--exclude" in args:
            exclude = {int(x) for x in args[args.index("--exclude") + 1].split(",") if x}
        targets = _since_targets(args[1], exclude)
    else:
        targets = [tuple(int(x) for x in a.split(":")) for a in args]
    print(f"targets (файлов к докачке): {len(targets)}", flush=True)

    client = TelegramClient(str(TG_SESSION_PATH), TG_API_ID, TG_API_HASH)
    await client.connect()
    ok = fail = skip = 0
    for chat_id, message_id in targets:
        try:
            msg = await client.get_messages(chat_id, ids=message_id)
        except Exception as e:
            print(f"  get {chat_id}/{message_id}: {type(e).__name__}: {e}", flush=True)
            fail += 1
            continue
        if not msg or not msg.media:
            skip += 1
            continue
        kind, file_name, size, mime = _detect_media(msg)
        if kind not in _DOWNLOAD_KINDS:
            skip += 1
            continue
        dt = msg.date.astimezone(timezone.utc)
        rel_dir = Path(TELEGRAM_STORAGE_ROOT) / f"{dt:%Y}" / f"{dt:%m}" / str(_abs_chat_id(chat_id))
        folder = STORAGE_DIR / rel_dir
        folder.mkdir(parents=True, exist_ok=True)
        base = file_name or _default_filename(kind, msg.id)
        full_name = f"{msg.id}_{_sanitize(base)}"
        to = min(1800, max(300, int((size or 0) / (30 * 1024))))  # size-aware
        try:
            await asyncio.wait_for(
                client.download_media(msg, file=str(folder / full_name)), timeout=to)
        except Exception as e:
            print(f"  dl {chat_id}/{message_id} ({(size or 0)/1048576:.0f}MB): "
                  f"{type(e).__name__}: {e}", flush=True)
            fail += 1
            continue
        rel = (rel_dir / full_name).as_posix()
        sb.table("tg_messages").update({
            "media_path": rel, "media_file_name": base,
            "media_mime": mime, "media_size": size,
        }).eq("chat_id", chat_id).eq("message_id", message_id).execute()
        ok += 1
        if ok % 50 == 0:
            print(f"  ... {ok} ok", flush=True)
    await client.disconnect()
    print(f"DONE ok={ok} fail={fail} skip={skip}", flush=True)


asyncio.run(main())
