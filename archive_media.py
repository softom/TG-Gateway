"""archive_media.py — ежемесячный перенос старых вложений с сервера в Dropbox.

Политика: на сервере хранятся вложения за KEEP_MONTHS календарных месяцев
(текущий + предыдущие); всё старше заливается в папку Dropbox-приложения
(синхронизируется на ПК: E:\\Dropbox\\Приложения\\<app>\\) и удаляется с сервера
ТОЛЬКО после сверки Dropbox content-hash.

Раскладка в Dropbox — по диалогам: /<Название диалога> [<abs_chat_id>]/<YYYY-MM>/<msg>_<name>.
Папка диалога закрепляется при первом переносе (по манифесту): переименование
чата в Telegram не плодит новых папок.

Перенесённые файлы пишутся в STORAGE_DIR/ARCHIVED.tsv (см. archive_manifest.py):
/api/v1/media отвечает по ним 410, rebackfill их не качает.

Креды (config.py, не в git): DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN.
Запуск:
    python archive_media.py --dry-run          # что будет перенесено
    python archive_media.py                    # перенести
    python archive_media.py --keep-months 4    # (по умолчанию 4)
Повторный запуск безопасен: файл с тем же хэшем в Dropbox не перезаливается.
"""
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import STORAGE_DIR
from archive_manifest import MANIFEST

BASE = Path(STORAGE_DIR).resolve()
CHUNK = 8 * 1024 * 1024
SIMPLE_MAX = 140 * 1024 * 1024
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _content_hash(path: Path) -> str:
    """Dropbox content_hash: sha256 от конкатенации sha256 блоков по 4 MB."""
    blocks = []
    with open(path, "rb") as f:
        while True:
            b = f.read(4 * 1024 * 1024)
            if not b:
                break
            blocks.append(hashlib.sha256(b).digest())
    return hashlib.sha256(b"".join(blocks)).hexdigest()


def _safe(name: str) -> str:
    """Имя папки, допустимое в Windows/Dropbox."""
    name = re.sub(r"\s+", " ", _BAD.sub("_", name or "")).strip()
    return name[:60].rstrip(". ") or "без названия"


def _chat_folders() -> dict:
    """abs_chat_id -> папка диалога: уже использованная (манифест), иначе по tg_chats."""
    from config import SUPABASE_URL, SUPABASE_SECRET_KEY
    from supabase import create_client
    from tg_raw_listener import _abs_chat_id
    sb = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    folders, s = {}, 0
    while True:
        ch = sb.table("tg_chats").select("chat_id,title").range(s, s + 999).execute().data or []
        for c in ch:
            a = str(_abs_chat_id(c["chat_id"]))
            folders[a] = f"{_safe(c.get('title'))} [{a}]"
        if len(ch) < 1000:
            break
        s += 1000
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            src = parts[0].split("/")
            dest = (parts[3] if len(parts) > 3 else "").split("/")
            if len(src) >= 5 and len(dest) >= 3 and dest[1].endswith(f"[{src[3]}]"):
                folders[src[3]] = dest[1]
    return folders


def _cutoff(keep: int) -> str:
    """Первый хранимый месяц 'YYYY/MM': текущий минус (keep-1)."""
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month - (keep - 1)
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}/{m:02d}"


def _upload(dbx, path: Path, dest: str):
    from dropbox.files import CommitInfo, UploadSessionCursor, WriteMode
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size <= SIMPLE_MAX:
            return dbx.files_upload(f.read(), dest, mode=WriteMode.overwrite)
        start = dbx.files_upload_session_start(f.read(CHUNK))
        cur = UploadSessionCursor(session_id=start.session_id, offset=f.tell())
        commit = CommitInfo(path=dest, mode=WriteMode.overwrite)
        while True:
            if size - cur.offset <= CHUNK:
                return dbx.files_upload_session_finish(f.read(CHUNK), cur, commit)
            dbx.files_upload_session_append_v2(f.read(CHUNK), cur)
            cur.offset = f.tell()


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    keep = int(args[args.index("--keep-months") + 1]) if "--keep-months" in args else 4
    cutoff = _cutoff(keep)
    tg = BASE / "TELEGRAM"
    months = sorted(f"{y.name}/{m.name}" for y in tg.iterdir() if y.is_dir()
                    for m in y.iterdir() if m.is_dir() and f"{y.name}/{m.name}" < cutoff)
    print(f"{datetime.now(timezone.utc):%F %T} keep={keep}, хранить с {cutoff}; "
          f"к переносу месяцев: {len(months)} {months}", flush=True)
    folders = _chat_folders()
    if dry:
        ids = sorted({d.name for mo in months for d in (tg / mo).iterdir() if d.is_dir()})
        print(f"  папок диалогов: {len(ids)}; без названия в реестре: "
              f"{sum(1 for i in ids if i not in folders)}", flush=True)
        for i in ids[:8]:
            print(f"    /{folders.get(i, f'без названия [{i}]')}/", flush=True)
        for mo in months:
            files = [p for p in (tg / mo).rglob("*") if p.is_file()]
            print(f"  {mo}: {len(files)} файлов, "
                  f"{sum(p.stat().st_size for p in files)/2**30:.2f} GB", flush=True)
        return

    import dropbox
    from config import DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN
    dbx = dropbox.Dropbox(app_key=DROPBOX_APP_KEY, app_secret=DROPBOX_APP_SECRET,
                          oauth2_refresh_token=DROPBOX_REFRESH_TOKEN, timeout=900)
    ok = fail = freed = 0
    for mo in months:
        for p in sorted(x for x in (tg / mo).rglob("*") if x.is_file()):
            rel = p.relative_to(BASE).as_posix()
            try:
                _, yy, mm, abs_id, fname = rel.split("/", 4)
                folder = folders.get(abs_id) or f"без названия [{abs_id}]"
                dest = f"/{folder}/{yy}-{mm}/{fname}"
                h = _content_hash(p)
                try:
                    md = dbx.files_get_metadata(dest)
                    same = getattr(md, "content_hash", None) == h
                except dropbox.exceptions.ApiError:
                    same = False
                if not same:
                    md = _upload(dbx, p, dest)
                if md.content_hash != h:
                    raise RuntimeError(f"hash mismatch {md.content_hash} != {h}")
                size = p.stat().st_size
                with open(MANIFEST, "a", encoding="utf-8") as mf:
                    mf.write(f"{rel}\t{size}\t{datetime.now(timezone.utc):%FT%TZ}\t{dest}\n")
                p.unlink()
                ok += 1
                freed += size
                if ok % 100 == 0:
                    print(f"  ... {ok} ok, освобождено {freed/2**30:.2f} GB", flush=True)
            except Exception as e:
                fail += 1
                print(f"  ✗ {rel}: {type(e).__name__}: {e}", flush=True)
        for d in sorted((tg / mo).rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        if (tg / mo).is_dir() and not any((tg / mo).iterdir()):
            (tg / mo).rmdir()
    print(f"DONE ok={ok} fail={fail}, освобождено {freed/2**30:.2f} GB", flush=True)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
