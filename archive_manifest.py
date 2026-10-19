"""Манифест архива медиа: файлы, перенесённые с сервера в Dropbox (archive_media.py).

STORAGE_DIR/ARCHIVED.tsv — строки `relpath \t size \t archived_at \t dropbox_path`,
relpath вида TELEGRAM/YYYY/MM/<abs_chat_id>/<message_id>_<name>.
Используют: /api/v1/media (410 вместо 404) и rebackfill_media (не качать заново).
"""
from pathlib import Path

from config import STORAGE_DIR

MANIFEST = Path(STORAGE_DIR) / "ARCHIVED.tsv"
_cache = {"mtime": None, "idx": {}}


def _index() -> dict:
    try:
        mt = MANIFEST.stat().st_mtime
    except FileNotFoundError:
        return {}
    if _cache["mtime"] != mt:
        idx = {}
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            p = parts[0].split("/")
            if len(p) >= 5 and "_" in p[4]:
                idx[(p[3], p[4].split("_", 1)[0])] = {
                    "month": f"{p[1]}-{p[2]}",
                    "archived_at": parts[2] if len(parts) > 2 else "",
                }
        _cache.update(mtime=mt, idx=idx)
    return _cache["idx"]


def archived(abs_chat_id, message_id):
    """{'month','archived_at'} если файл сообщения перенесён в архив, иначе None."""
    return _index().get((str(abs_chat_id), str(message_id)))
