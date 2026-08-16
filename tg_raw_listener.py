"""
tg_raw_listener.py — RAW Telegram-ingest для внешнего US-сервера.

Назначение: ловит ВСЕ диалоги (без whitelist) под личным аккаунтом (MTProto,
Telethon) и пишет СЫРЬЁ в внешний Supabase (tg_chats + tg_messages). Медиа
качает в STORAGE_DIR (с контролем свободного места). КЛАССИФИКАЦИИ НЕТ —
её делает локальный ЗПР, читая из облака.

Архитектура: см. /opt/zpr/docs/00_SERVER.md.

Команды:
  --auth            авторизация (телефон+код) — обычно не нужна, сессию копируем с локали
  --backfill DAYS   забрать историю ВСЕХ диалогов за N дней
  --listen          слушать новые сообщения ВО ВСЕХ чатах

Config (серверный config.py): TG_API_ID, TG_API_HASH, TG_SESSION_PATH,
STORAGE_DIR, SUPABASE_URL, SUPABASE_SECRET_KEY.
"""

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from telethon import TelegramClient, events
from telethon.tl.types import (
    Channel, Chat, User, Message,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaPoll,
    MessageMediaContact, MessageMediaGeo, MessageMediaWebPage,
    DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio,
    DocumentAttributeSticker, DocumentAttributeAnimated,
)

from config import (
    TG_API_ID, TG_API_HASH, TG_SESSION_PATH,
    STORAGE_DIR, SUPABASE_URL, SUPABASE_SECRET_KEY,
)

TELEGRAM_STORAGE_ROOT = "TELEGRAM"
DISK_MIN_FREE_GB = 5.0          # ниже этого порога медиа не качаем (только метаданные) — оставляем запас на ОС/логи/swap
_DOWNLOAD_KINDS = {"photo", "document", "video", "audio", "voice", "sticker", "gif"}
_low_disk_warned = False


# ─── Клиенты ─────────────────────────────────────────────────────────────────

def _make_client() -> TelegramClient:
    if not TG_API_ID or not TG_API_HASH:
        sys.exit("config.TG_API_ID / TG_API_HASH не заполнены.")
    return TelegramClient(str(TG_SESSION_PATH), TG_API_ID, TG_API_HASH)


def _make_sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)


def _disk_free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / (1024 ** 3)
    except Exception:
        return 999.0


# ─── Helpers (перенесены из telegram_listener.py) ────────────────────────────

def _entity_kind(entity) -> str:
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, User):
        return "user"
    return "group"


# ─── Фильтр захвата: исключаем публичный шум ──────────────────────────────────
# По умолчанию НЕ берём: broadcast-каналы (новости/анонсы) и ПУБЛИЧНЫЕ
# (с @username) группы/супергруппы — это публичные сообщества.
# Берём: личные чаты (ЛС) и приватные группы/супергруппы (рабочие/проектные).
# ЧЁРНЫЙ СПИСОК: берём ВСЁ, кроме перечисленного здесь + авто-исключений ниже.
EXCLUDE_CHAT_IDS: set[int] = {
    # ── Утверждённый чёрный список (публичные каналы/сообщества — шум) ──
    -1001867238588,  # AmanRA ☀️
    -1001748711277,  # CGPlugins
    -1001681423479,  # Files каналов CGPlugins и CG Курсы
    -1002617662605,  # GeoSurveyor
    -1001685069954,  # GIS AND PEACE
    -1001904018622,  # How2AI
    -1001528281033,  # Konstantin Zherenkov
    -1001314600216,  # R4marketing
    -1003276340458,  # RELAX пуховая мягкая мебель
    -1001725056474,  # SofTop
    -1001683522599,  # Soilbox
    -1001220784339,  # StudyAI | Нейросети
    -1001760888539,  # URBAN MASH
    -1001153922447,  # Бельбекская Долина. Анонсы
    -1001081521468,  # ГеоИнфо
    -1001320398635,  # ГородаМеняютсяДляНас
    -1001206168996,  # Деревня Мастеров «Пания Парк»
    -1001578080949,  # доказательный пробел
    -1002494738691,  # дядя_д
    -1001955874033,  # Иванкин
    -1002364578926,  # Картотекарь
    -1002198042077,  # Креативный совет
    -1002055601376,  # Маслоbrot
    -1003202881562,  # МАЯК
    -1001715218159,  # Михаил Кукин
    -1001871868311,  # Новости мерзлотной карты ЯНАО 2023
    -1001933500399,  # Панин о городах и людях
    -1001605558264,  # Пою Пеку
    -1001719369115,  # Селигерский мед
    -1001943757531,  # Случайное блуждание
    -1002035519868,  # Союз Архитекторов Севастополя
    -1001675149361,  # Транспорт Севастополя
    -1001945128190,  # Центр социальной реабилитации «Родник»
    -1001364070356,  # ЧП / Севастополь
    -1001395731949,  # Belbek People
    -1001706025553,  # GC Transfer | Новости сервиса
    -1001699917144,  # Hodit.net | рейд в Севастополе
    -1001430858573,  # Krasnaya Polyana FM radio
    -1002615438852,  # Polza.ai • Чат community
    -1002514514081,  # WebGeo
    -1002428281064,  # Жители Куйбышево (Крым)
    -1002955402379,  # Центр недвижимости Бельбекской долины
}
INCLUDE_CHAT_IDS: set[int] = set()   # override: всегда брать, даже если попало под авто-исключение


def _should_capture(entity, chat_id: int) -> bool:
    if chat_id in INCLUDE_CHAT_IDS:      # override: всегда брать
        return True
    if chat_id in EXCLUDE_CHAT_IDS:      # чёрный список: исключить вручную
        return False
    kind = _entity_kind(entity)
    if kind == "channel":                                    # broadcast-канал — шум
        return False
    if kind in ("group", "supergroup") and getattr(entity, "username", None):
        return False                                         # публичное сообщество (@username)
    return True                                              # ЛС и приватные группы/супергруппы


def _abs_chat_id(chat_id: int) -> int:
    cid = abs(chat_id)
    s = str(cid)
    if len(s) >= 13 and s.startswith("100"):
        return int(s[3:])
    return cid


def _detect_media(msg: Message):
    media = msg.media
    if media is None:
        return None, None, None, None
    if isinstance(media, MessageMediaPhoto):
        return "photo", None, None, "image/jpeg"
    if isinstance(media, MessageMediaPoll):
        return "poll", None, None, None
    if isinstance(media, MessageMediaContact):
        return "contact", None, None, None
    if isinstance(media, MessageMediaGeo):
        return "geo", None, None, None
    if isinstance(media, MessageMediaWebPage):
        return "webpage", None, None, None
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc is None:
            return "other", None, None, None
        mime = doc.mime_type or ""
        size = doc.size
        file_name = None
        is_voice = is_video = is_audio = is_sticker = is_animated = False
        for attr in (doc.attributes or []):
            if isinstance(attr, DocumentAttributeFilename):
                file_name = attr.file_name
            elif isinstance(attr, DocumentAttributeVideo):
                is_video = True
            elif isinstance(attr, DocumentAttributeAudio):
                is_audio = True
                is_voice = bool(getattr(attr, "voice", False))
            elif isinstance(attr, DocumentAttributeSticker):
                is_sticker = True
            elif isinstance(attr, DocumentAttributeAnimated):
                is_animated = True
        if is_sticker:
            kind = "sticker"
        elif is_animated:
            kind = "gif"
        elif is_voice:
            kind = "voice"
        elif is_video:
            kind = "video"
        elif is_audio:
            kind = "audio"
        else:
            kind = "document"
        return kind, file_name, size, mime
    return "other", None, None, None


def _default_filename(kind: str, message_id: int) -> str:
    return {
        "photo":   f"photo_{message_id}.jpg",
        "voice":   f"voice_{message_id}.ogg",
        "video":   f"video_{message_id}.mp4",
        "audio":   f"audio_{message_id}.mp3",
        "sticker": f"sticker_{message_id}.webp",
        "gif":     f"animation_{message_id}.mp4",
    }.get(kind, f"file_{message_id}.bin")


def _sanitize(name: str) -> str:
    return "".join(c for c in name if c not in '\\/:*?"<>|\n\r\t')[:200] or "file"


def _sender_info(msg: Message):
    sender = msg.sender
    if sender is None:
        return None, None, None
    sender_id = msg.sender_id
    sender_username = getattr(sender, "username", None)
    if isinstance(sender, User):
        parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
        sender_name = " ".join(p for p in parts if p) or None
    else:
        sender_name = getattr(sender, "title", None)
    return sender_id, sender_username, sender_name


def _reply_info(msg: Message):
    rt = getattr(msg, "reply_to", None)
    if rt is None:
        return None, None
    is_topic = bool(getattr(rt, "forum_topic", False))
    top_id = getattr(rt, "reply_to_top_id", None)
    msg_id = getattr(rt, "reply_to_msg_id", None)
    if is_topic:
        return top_id, (msg_id if msg_id != top_id else None)
    return None, msg_id


# ─── Запись в БД ─────────────────────────────────────────────────────────────

def _chat_title(entity, fallback: str | None = None) -> str | None:
    return (getattr(entity, "title", None)
            or f"{getattr(entity, 'first_name', '') or ''} {getattr(entity, 'last_name', '') or ''}".strip()
            or fallback)


async def _upsert_chat(sb, entity, chat_id: int, handler: str = "raw") -> None:
    row = {
        "chat_id": chat_id,
        "title": _chat_title(entity),
        "username": getattr(entity, "username", None),
        "kind": _entity_kind(entity),
        "is_whitelisted": True,
        "handler": handler,
    }
    try:
        sb.table("tg_chats").upsert(row, on_conflict="chat_id").execute()
    except Exception as exc:
        print(f"  ⚠ upsert chat {chat_id}: {exc}", flush=True)


async def _save_message(sb, client, msg: Message, chat_id: int) -> dict | None:
    """Сохраняет одно сообщение. Возвращает строку или None (дубль/ошибка)."""
    global _low_disk_warned
    kind, file_name, size, mime = _detect_media(msg)
    has_media = kind is not None
    media_rel_path = None
    final_file_name = file_name

    if has_media and kind in _DOWNLOAD_KINDS:
        if _disk_free_gb(STORAGE_DIR) < DISK_MIN_FREE_GB:
            if not _low_disk_warned:
                print(f"  ⚠ свободно <{DISK_MIN_FREE_GB} GB — медиа НЕ качаю, только метаданные", flush=True)
                _low_disk_warned = True
        else:
            dt = msg.date.astimezone(timezone.utc)
            rel_dir = Path(TELEGRAM_STORAGE_ROOT) / f"{dt:%Y}" / f"{dt:%m}" / str(_abs_chat_id(chat_id))
            folder = STORAGE_DIR / rel_dir
            folder.mkdir(parents=True, exist_ok=True)
            base = file_name or _default_filename(kind, msg.id)
            full_name = f"{msg.id}_{_sanitize(base)}"
            try:
                _dl_to = min(1800, max(300, int((size or 0) / (30 * 1024))))  # size-aware: ~30KB/s пол, 5..30 мин
                await asyncio.wait_for(
                    client.download_media(msg, file=str(folder / full_name)), timeout=_dl_to)
                media_rel_path = (rel_dir / full_name).as_posix()
                final_file_name = base
            except (Exception, asyncio.CancelledError) as exc:
                # CancelledError (BaseException) от Telethon при обрыве/таймауте скачивания
                # не должна ронять весь backfill — логируем и продолжаем без файла.
                print(f"  ⚠ media download {chat_id}/{msg.id}: {type(exc).__name__}: {exc}", flush=True)

    sender_id, sender_username, sender_name = _sender_info(msg)
    thread_id, reply_to_msg_id = _reply_info(msg)
    try:
        raw_json = json.loads(msg.to_json())
    except Exception:
        raw_json = None

    row = {
        "chat_id": chat_id,
        "message_id": msg.id,
        "thread_id": thread_id,
        "reply_to_msg_id": reply_to_msg_id,
        "sender_id": sender_id,
        "sender_username": sender_username,
        "sender_name": sender_name,
        "msg_date": msg.date.isoformat(),
        "edit_date": msg.edit_date.isoformat() if msg.edit_date else None,
        "text": msg.message or None,
        "has_media": has_media,
        "media_kind": kind,
        "media_path": media_rel_path,
        "media_file_name": final_file_name,
        "media_mime": mime,
        "media_size": size,
        "raw_json": raw_json,
    }
    try:
        resp = sb.table("tg_messages").insert(row).execute()
        return resp.data[0] if resp.data else None
    except Exception as exc:
        text = str(exc)
        if "23505" in text or "duplicate" in text.lower():
            # Дубликат: если в этот раз файл скачан (media_rel_path есть), а в уже
            # существующей строке media_path пуст — дозаполняем медиа-поля.
            # `.is_("media_path","null")` гарантирует, что НЕ затираем уже записанное.
            if media_rel_path:
                try:
                    (sb.table("tg_messages").update({
                        "media_path": media_rel_path,
                        "media_file_name": final_file_name,
                        "media_mime": mime,
                        "media_size": size,
                    }).eq("chat_id", chat_id).eq("message_id", msg.id)
                      .is_("media_path", "null").execute())
                except Exception as e2:
                    print(f"  ⚠ media_path fill {chat_id}/{msg.id}: {e2}", flush=True)
            return None
        print(f"  ⚠ insert {chat_id}/{msg.id}: {exc}", flush=True)
        return None


# ─── Команды ─────────────────────────────────────────────────────────────────

async def cmd_auth() -> None:
    client = _make_client()
    await client.start()
    me = await client.get_me()
    print(f"Авторизован: {me.first_name} {me.last_name or ''} (@{me.username or '—'}, id={me.id})")
    await client.disconnect()


async def cmd_list_chats() -> None:
    """Перечислить ВСЕ диалоги с пометкой текущего фильтра (KEEP/SKIP) — для курирования."""
    client = _make_client()
    await client.start()
    rows = []
    async for dialog in client.iter_dialogs():
        ent = dialog.entity
        cid = dialog.id
        kind = _entity_kind(ent)
        pub = bool(getattr(ent, "username", None))
        verdict = "KEEP" if _should_capture(ent, cid) else "SKIP"
        last = dialog.date.strftime("%Y-%m-%d") if dialog.date else "-"
        rows.append((verdict, kind, pub, cid, (dialog.name or "?"), last))
    rows.sort(key=lambda r: (r[0] != "SKIP", r[1], r[4].lower()))
    print(f"{'VERDICT':<7} {'KIND':<11} {'PUB':<4} {'CHAT_ID':>15} {'LAST':<10} TITLE", flush=True)
    print("-" * 92, flush=True)
    for verdict, kind, pub, cid, title, last in rows:
        print(f"{verdict:<7} {kind:<11} {'pub' if pub else 'priv':<4} {cid:>15} {last:<10} {title[:55]}", flush=True)
    n_skip = sum(1 for r in rows if r[0] == "SKIP")
    print(f"\nИтого: {len(rows)} диалогов. SKIP={n_skip}, KEEP={len(rows) - n_skip}", flush=True)
    await client.disconnect()


async def cmd_backfill(days: int) -> None:
    client = _make_client()
    sb = _make_sb()
    await client.start()
    me = await client.get_me()
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Авторизован: {me.first_name} {me.last_name or ''}. Backfill ВСЕХ диалогов за {days} дн.", flush=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total_new = total_dup = n_chats = n_skipped = 0
    async for dialog in client.iter_dialogs():
        cid = dialog.id
        if not _should_capture(dialog.entity, cid):
            n_skipped += 1
            continue
        await _upsert_chat(sb, dialog.entity, cid)
        n_chats += 1
        new = dup = 0
        try:
            async for msg in client.iter_messages(cid):
                if msg.date.astimezone(timezone.utc) < cutoff:
                    break
                if await _save_message(sb, client, msg, cid):
                    new += 1
                else:
                    dup += 1
        except Exception as exc:
            print(f"  ⚠ iter {cid}: {exc}", flush=True)
        if new or dup:
            name = (dialog.name or "?")[:40]
            print(f"  {cid} '{name}': +{new}/{dup}  (free {_disk_free_gb(STORAGE_DIR):.1f}GB)", flush=True)
        total_new += new
        total_dup += dup

    print(f"Готово: чатов {n_chats} (пропущено публичных {n_skipped}), "
          f"{total_new} новых, {total_dup} дублей. "
          f"Свободно {_disk_free_gb(STORAGE_DIR):.1f}GB.", flush=True)
    await client.disconnect()


async def cmd_listen() -> None:
    client = _make_client()
    sb = _make_sb()
    await client.start()
    me = await client.get_me()
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Авторизован: {me.first_name} {me.last_name or ''}. Слушаю ВСЕ чаты (raw). Ctrl+C — стоп.", flush=True)

    seen_chats: set[int] = set()
    skip_chats: set[int] = set()

    @client.on(events.NewMessage())
    async def _handler(event):
        cid = event.chat_id
        if cid is None or cid in skip_chats:
            return
        try:
            if cid not in seen_chats:
                try:
                    ent = await event.get_chat()
                except Exception as exc:
                    print(f"  ⚠ get_chat {cid}: {exc}", flush=True)
                    return
                if not _should_capture(ent, cid):
                    skip_chats.add(cid)
                    return
                await _upsert_chat(sb, ent, cid)
                seen_chats.add(cid)
            saved = await _save_message(sb, client, event.message, cid)
            if saved:
                preview = ((event.message.message or "")[:60]).replace("\n", " ")
                kind, *_ = _detect_media(event.message)
                tag = f"[{kind}] " if kind else ""
                print(f"  + {cid} #{event.message.id} {tag}{preview}", flush=True)
        except Exception as exc:
            print(f"  ⚠ handler {cid}: {exc}", flush=True)

    await client.run_until_disconnected()
    # Daemon не должен завершаться штатно — если мы здесь, соединение оборвалось.
    raise RuntimeError("run_until_disconnected returned — соединение оборвалось")


def main() -> None:
    p = argparse.ArgumentParser(description="RAW Telegram listener для US-сервера ЗПР")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--auth", action="store_true", help="авторизация (телефон+код)")
    g.add_argument("--list-chats", action="store_true", help="перечислить диалоги с пометкой KEEP/SKIP")
    g.add_argument("--backfill", type=int, metavar="DAYS", help="история ВСЕХ диалогов за N дней")
    g.add_argument("--listen", action="store_true", help="слушать новые сообщения ВО ВСЕХ чатах")
    args = p.parse_args()

    if args.auth:
        asyncio.run(cmd_auth())
    elif args.list_chats:
        asyncio.run(cmd_list_chats())
    elif args.backfill is not None:
        asyncio.run(cmd_backfill(args.backfill))
    elif args.listen:
        try:
            asyncio.run(cmd_listen())
        except KeyboardInterrupt:
            print("Listener: Ctrl+C, выход", flush=True)
            sys.exit(0)
        except Exception as exc:
            print(f"Listener сбой: {type(exc).__name__}: {exc}", flush=True)
            sys.exit(1)
        print("Listener: cmd_listen неожиданно завершился — выход для рестарта", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
