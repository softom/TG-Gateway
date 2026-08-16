"""
send_telegram_protocol.py — отправка .docx-протокола в whitelisted
Telegram-чаты под личным аккаунтом (Telethon / MTProto).

Запускается из Next.js (POST /api/protocols/[id]/publish) через
child_process.spawn.

──────────────────────────────────────────────────────────────────────────────
Session-файл:

По умолчанию используется тот же session, что и у telegram_listener.py
(config.TG_SESSION_PATH). Listener в основном читает events и редко
пишет в SQLite-сессию (только при auth/reconnect); короткие send-операции
к нему не приводят к конфликту lock'а — SQLite в Telethon работает в WAL.

Если потребуется развести их (например, на случай частых ошибок lock-а),
задайте в config.py отдельный путь TG_SESSION_SEND_PATH — скрипт его
автоматически подхватит как override.
──────────────────────────────────────────────────────────────────────────────

Аргументы:
   --chat-ids "id1,id2,..."   — список Telegram chat_id через запятую
   --file PATH                — путь к .docx-файлу
   --caption "TEXT"           — подпись (UTF-8)

Выход (stdout):
   JSON {"results": [{"chat_id": -100123, "status": "ok",
                      "message_id": 456}, ...]}

Ошибки конкретного чата не прерывают остальные — каждому свой статус.
Глобальная ошибка (нет session, не подключиться) — exit code 1 + JSON
с error в stderr.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Чтобы импорт config работал когда скрипт запускают из любой директории.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telethon import TelegramClient  # noqa: E402
from telethon.errors import (        # noqa: E402
    FloodWaitError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
)

try:
    from config import TG_API_ID, TG_API_HASH, TG_SESSION_PATH  # type: ignore
except ImportError as e:
    print(json.dumps({"error": f"Не найден config.py: {e}"}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)

# По умолчанию используем основную session (тот же файл, что у listener).
# Если в config задан TG_SESSION_SEND_PATH — используем его как override
# (на случай частых конфликтов SQLite-lock).
try:
    from config import TG_SESSION_SEND_PATH as _SEND_PATH  # type: ignore
    SESSION_TO_USE = _SEND_PATH
except ImportError:
    SESSION_TO_USE = TG_SESSION_PATH


async def send_one(client: TelegramClient, chat_id: int, file_path: str, caption: str) -> dict:
    """Отправляет файл с подписью в один чат. Возвращает dict-результат."""
    try:
        msg = await client.send_file(
            entity=chat_id,
            file=file_path,
            caption=caption,
            force_document=True,   # .docx всегда как документ, не как фото
        )
        return {"chat_id": chat_id, "status": "ok", "message_id": msg.id}
    except FloodWaitError as e:
        return {"chat_id": chat_id, "status": "fail",
                "error": f"FloodWait: подождите {e.seconds} сек"}
    except ChatWriteForbiddenError:
        return {"chat_id": chat_id, "status": "fail",
                "error": "Нет прав на запись в этот чат"}
    except ChannelPrivateError:
        return {"chat_id": chat_id, "status": "fail",
                "error": "Аккаунт не состоит в этом чате"}
    except Exception as e:
        return {"chat_id": chat_id, "status": "fail",
                "error": f"{type(e).__name__}: {e}"}


async def main_async(chat_ids: list[int], file_path: str, caption: str) -> None:
    if not TG_API_ID or not TG_API_HASH:
        print(json.dumps({"error": "config.TG_API_ID / TG_API_HASH не заполнены"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    if not Path(file_path).exists():
        print(json.dumps({"error": f"Файл не найден: {file_path}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    session_path = Path(str(SESSION_TO_USE))
    # Telethon добавляет .session к пути, поэтому проверяем оба варианта.
    if not (session_path.exists() or session_path.with_suffix(".session").exists()):
        print(json.dumps(
            {"error": (
                f"Telegram-сессия не найдена: {session_path}. "
                "Сначала авторизуйте listener: python telegram_listener.py --auth"
            )},
            ensure_ascii=False,
        ), file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(str(session_path), TG_API_ID, TG_API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print(json.dumps(
                {"error": "Send-session не авторизована — повторите --auth-send"},
                ensure_ascii=False,
            ), file=sys.stderr)
            sys.exit(1)

        results = []
        for cid in chat_ids:
            results.append(await send_one(client, cid, file_path, caption))
        print(json.dumps({"results": results}, ensure_ascii=False))
    finally:
        await client.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(description="Send .docx protocol to Telegram chats.")
    p.add_argument("--chat-ids", required=True,
                   help='Список chat_id через запятую (например "-1001234,-1005678")')
    p.add_argument("--file", required=True, help="Путь к .docx-файлу")
    p.add_argument("--caption", required=True, help="Подпись (UTF-8)")
    args = p.parse_args()

    try:
        chat_ids = [int(x) for x in args.chat_ids.split(",") if x.strip()]
    except ValueError as e:
        print(json.dumps({"error": f"Неправильный chat-ids: {e}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    if not chat_ids:
        print(json.dumps({"error": "Пустой список chat-ids"}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(1)

    asyncio.run(main_async(chat_ids, args.file, args.caption))


if __name__ == "__main__":
    main()
