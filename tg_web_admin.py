"""
tg_web_admin.py — Web-админка TG-листенера (US-сервер).

FastAPI + Jinja2. Порт 8080.
  - Дашборд: статус синхронизации, счётчики, лог.
  - Клиенты: CRUD API-клиентов с ключами.
  - Доступ: per-client чат-разрешения (read / write).
  - Client API: /api/v1/* — внешние приложения читают данные по API-ключу.

Запуск:
  python tg_web_admin.py                          # dev
  uvicorn tg_web_admin:app --host 0.0.0.0 --port 8080  # prod

Переменные (config.py или env):
  ADMIN_TOKEN  — токен доступа к админ-панели (обязателен, ≥16 символов; без него сервис не стартует).
"""

import os
import asyncio
import secrets
import subprocess
import shutil
import mimetypes
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException, Query, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client as sb_create

from config import SUPABASE_URL, SUPABASE_SECRET_KEY, STORAGE_DIR

# ── app ──────────────────────────────────────────────────────────────
app = FastAPI(title="ZPR TG Admin", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "tg_web" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "tg_web" / "templates")

sb = sb_create(SUPABASE_URL, SUPABASE_SECRET_KEY)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN",
                              getattr(__import__("config"), "ADMIN_TOKEN", ""))
# Fail-closed: без настоящего токена админка и /ctl/* не стартуют.
if len(ADMIN_TOKEN) < 16 or ADMIN_TOKEN.lower() in ("changeme", "change_me", "__set_at_deploy__"):
    raise RuntimeError("ADMIN_TOKEN не задан или слабый (нужно ≥16 символов) — "
                       "задайте в systemd Environment=ADMIN_TOKEN или config.py")

# ── helpers ──────────────────────────────────────────────────────────
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _service_status() -> dict:
    """Статус systemd-сервиса листенера."""
    info = {"active": "unknown", "uptime": "", "memory": ""}
    try:
        r = subprocess.run(
            ["systemctl", "show", "zpr-tg-listener",
             "--property=ActiveState,SubState,ActiveEnterTimestamp,MemoryCurrent"],
            capture_output=True, text=True, timeout=5)
        for line in r.stdout.strip().splitlines():
            k, _, v = line.partition("=")
            if k == "ActiveState":
                info["active"] = v
            elif k == "ActiveEnterTimestamp" and v:
                info["uptime"] = v
            elif k == "MemoryCurrent" and v.isdigit():
                info["memory"] = f"{int(v) / (1024*1024):.1f} MB"
    except Exception:
        pass
    return info


def _disk_info() -> Optional[dict]:
    for p in [Path("/opt/zpr/storage"), Path("/opt/zpr"), Path("/")]:
        if p.exists():
            u = shutil.disk_usage(p)
            return {"total": round(u.total / 2**30, 1),
                    "used": round(u.used / 2**30, 1),
                    "free": round(u.free / 2**30, 1),
                    "pct": round(u.used / u.total * 100)}
    return None


def _log_tail(n: int = 40) -> list[str]:
    for p in [Path("/opt/zpr/logs/listener.log"), Path("/opt/zpr/logs/backfill.log")]:
        if p.exists():
            try:
                lines = p.read_text(errors="replace").splitlines()
                if lines:
                    return lines[-n:]
            except Exception:
                pass
    return []


def _backfill_status() -> dict:
    """Статус процесса backfill."""
    info = {"running": False, "pid": None}
    try:
        r = subprocess.run(
            ["pgrep", "-f", "tg_raw_listener.py --backfill"],
            capture_output=True, text=True, timeout=5)
        pids = r.stdout.strip().splitlines()
        if pids:
            info["running"] = True
            info["pid"] = pids[0]
    except Exception:
        pass
    return info


def _archive_status() -> dict:
    """Бэкап вложений в Dropbox (tg-archive.service/.timer, archive_media.py)."""
    info = {"state": "?", "next": "", "last": "", "archived": 0, "archived_gb": 0.0}
    try:
        info["state"] = subprocess.run(
            ["systemctl", "is-active", "tg-archive.service"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        info["next"] = subprocess.run(
            ["systemctl", "show", "tg-archive.timer", "-p", "NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    log = Path("/opt/zpr/logs/archive.log")
    if log.exists():
        try:
            lines = [l for l in log.read_text(errors="replace").splitlines()
                     if l.startswith("DONE") or "хранить с" in l or l.lstrip().startswith("...")]
            info["last"] = " · ".join(lines[-2:])
        except Exception:
            pass
    try:
        from archive_manifest import MANIFEST
        if MANIFEST.exists():
            rows = [l.split("\t") for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l]
            info["archived"] = len(rows)
            info["archived_gb"] = round(sum(int(r[1]) for r in rows
                                            if len(r) > 1 and r[1].isdigit()) / 2**30, 2)
    except Exception:
        pass
    return info


# ── auth ─────────────────────────────────────────────────────────────
def require_admin(request: Request):
    """Проверка admin-токена: cookie или ?token=... → /login при отсутствии."""
    token = request.cookies.get("admin_token") or request.query_params.get("token")
    if not token or token != ADMIN_TOKEN:
        # Для HTML-страниц — redirect, для API — 401
        if "api/" in request.url.path:
            raise HTTPException(status_code=401, detail="Unauthorized")
        raise HTTPException(status_code=307,
                            headers={"Location": "/login"})
    return token


def require_client(request: Request):
    """Проверка API-ключа клиента: заголовок X-Api-Key или ?api_key=..."""
    api_key = request.headers.get("X-Api-Key") or request.query_params.get("api_key")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required (X-Api-Key header)")
    result = sb.table("tg_clients").select("*").eq("api_key", api_key).eq("is_active", True).execute()
    if not result.data:
        raise HTTPException(status_code=403, detail="Invalid or inactive API key")
    return result.data[0]


# ── login ────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login_submit(request: Request, token: str = Form(...)):
    if token != ADMIN_TOKEN:
        return templates.TemplateResponse(request=request, name="login.html",
                                          context={"error": "Неверный токен"})
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("admin_token", token, httponly=True, max_age=86400 * 7)
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("admin_token")
    return resp


# ── dashboard ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_admin)):
    service = _service_status()
    backfill = _backfill_status()
    archive = _archive_status()
    disk = _disk_info()

    # Все БД-запросы обёрнуты: tg_messages большая, на free-tier Supabase медленный
    # count/order может упереться в statement_timeout (57014) — дашборд не должен
    # из-за этого падать 500, пусть деградирует по счётчикам.
    def _q(fn, default):
        try:
            return fn()
        except Exception as exc:
            print(f"  ⚠ dashboard query: {type(exc).__name__}: {exc}", flush=True)
            return default

    # Чаты
    chats = _q(lambda: sb.table("tg_chats").select("*").order("title").execute().data, None) or []

    # Счётчики: total — ESTIMATED (мгновенно; exact COUNT(*) по огромной таблице = timeout)
    total = _q(lambda: sb.table("tg_messages").select("id", count="estimated").execute().count, "?")
    today_iso = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
    today_count = _q(lambda: (sb.table("tg_messages").select("id", count="exact")
                              .gte("ingested_at", today_iso).execute().count), "?")

    # Последние сообщения
    recent = _q(lambda: (sb.table("tg_messages")
                         .select("chat_id,message_id,sender_name,msg_date,text")
                         .order("msg_date", desc=True).limit(25).execute().data), None) or []

    # Подмешиваем название чата
    chat_map = {c["chat_id"]: c.get("title", str(c["chat_id"])) for c in chats}
    for m in recent:
        m["_chat_title"] = chat_map.get(m["chat_id"], str(m["chat_id"]))

    log_lines = _log_tail(40)

    # Клиенты (count) — таблица крошечная, exact безопасен
    clients_count = _q(lambda: sb.table("tg_clients").select("id", count="exact").execute().count, "?")

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "service": service,
        "backfill": backfill,
        "archive": archive,
        "disk": disk,
        "chats": chats,
        "chat_count": len(chats),
        "total_messages": total,
        "today_messages": today_count,
        "clients_count": clients_count,
        "recent": recent,
        "log_lines": log_lines,
    })


# ── service controls ─────────────────────────────────────────────────
@app.post("/ctl/listener/start")
async def ctl_listener_start(_=Depends(require_admin)):
    subprocess.run(["systemctl", "start", "zpr-tg-listener"], timeout=10)
    return RedirectResponse("/", status_code=303)


@app.post("/ctl/listener/stop")
async def ctl_listener_stop(_=Depends(require_admin)):
    subprocess.run(["systemctl", "stop", "zpr-tg-listener"], timeout=10)
    return RedirectResponse("/", status_code=303)


@app.post("/ctl/backfill/start")
async def ctl_backfill_start(_=Depends(require_admin), days: int = Form(365)):
    """Запустить backfill в фоне (nice -n 19)."""
    bf = _backfill_status()
    if bf["running"]:
        return RedirectResponse("/", status_code=303)  # уже идёт
    subprocess.Popen(
        ["nice", "-n", "19", "ionice", "-c", "3",
         "/opt/zpr/venv/bin/python", "/opt/zpr/app/tg_raw_listener.py",
         "--backfill", str(days)],
        stdout=open("/opt/zpr/logs/backfill.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return RedirectResponse("/", status_code=303)


@app.post("/ctl/backfill/stop")
async def ctl_backfill_stop(_=Depends(require_admin)):
    """Остановить backfill (SIGTERM)."""
    subprocess.run(["pkill", "-f", "tg_raw_listener.py --backfill"], timeout=10)
    return RedirectResponse("/", status_code=303)


# ── публикация: отправка файла в чаты (Telethon) ─────────────────────
_send_lock = asyncio.Lock()


@app.post("/ctl/archive/start")
async def ctl_archive_start(_=Depends(require_admin)):
    """Бэкап вложений старше 4 мес. в Dropbox — тот же сервис, что запускает таймер."""
    subprocess.run(["systemctl", "start", "--no-block", "tg-archive.service"], timeout=10)
    return RedirectResponse("/", status_code=303)


@app.post("/ctl/archive/stop")
async def ctl_archive_stop(_=Depends(require_admin)):
    """Прервать бэкап. Безопасно: файл удаляется с сервера только после сверки хэша."""
    subprocess.run(["systemctl", "stop", "--no-block", "tg-archive.service"], timeout=10)
    return RedirectResponse("/", status_code=303)


@app.post("/ctl/send-file")
async def ctl_send_file(
    request: Request,
    file: UploadFile = File(...),
    chat_ids: str = Form(...),
    caption: str = Form(""),
):
    """Рассылка файла (.docx-протокол и т.п.) в Telegram-чаты под аккаунтом
    листенера. multipart: file, chat_ids (через запятую), caption.
    Ошибка одного чата не прерывает остальные.
    Ответ: {"results":[{chat_id,status,message_id|error}, ...]}.

    Auth: admin_token (cookie `admin_token` или `?token=`). При плохом/пустом
    токене — 403 {"error":"unauthorized"} (НЕ 307-редирект, как у браузерных
    /ctl/*), чтобы бэкенду-вызывающему был понятен сбой авторизации.
    Сессия: по умолчанию та же, что у листенера (config.TG_SESSION_PATH);
    если задан TG_SESSION_SEND_PATH — используется он (на случай SQLite-lock).
    Отправки сериализуются _send_lock, чтобы не открывать два клиента разом."""
    token = request.cookies.get("admin_token") or request.query_params.get("token")
    if not token or token != ADMIN_TOKEN:
        return JSONResponse(status_code=403, content={"error": "unauthorized"})
    import os
    import tempfile
    from config import TG_API_ID, TG_API_HASH, TG_SESSION_PATH
    try:
        from config import TG_SESSION_SEND_PATH as _SESS
    except ImportError:
        _SESS = TG_SESSION_PATH
    from telethon import TelegramClient
    from telethon.errors import (FloodWaitError, ChatWriteForbiddenError,
                                 ChannelPrivateError)
    from telethon.tl.types import DocumentAttributeFilename

    try:
        ids = [int(x) for x in chat_ids.split(",") if x.strip()]
    except ValueError as e:
        raise HTTPException(400, f"bad chat_ids: {e}")
    if not ids:
        raise HTTPException(400, "empty chat_ids")

    data = await file.read()
    # Имя документа в Telegram = orig_name. Иначе Telethon берёт basename
    # временного файла («tgsend_xxxx.docx» — нечитаемо для получателя).
    orig_name = os.path.basename(file.filename or "protocol.docx") or "protocol.docx"
    suffix = os.path.splitext(orig_name)[1] or ".docx"
    fd, tmp = tempfile.mkstemp(prefix="tgsend_", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)

    results = []
    try:
        async with _send_lock:
            client = TelegramClient(str(_SESS), TG_API_ID, TG_API_HASH)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise HTTPException(503, "send-session не авторизована")
                for cid in ids:
                    try:
                        msg = await client.send_file(
                            cid, tmp, caption=caption, force_document=True,
                            attributes=[DocumentAttributeFilename(orig_name)])
                        results.append({"chat_id": cid, "status": "ok",
                                        "message_id": msg.id})
                    except FloodWaitError as e:
                        results.append({"chat_id": cid, "status": "fail",
                                        "error": f"FloodWait: подождите {e.seconds} сек"})
                    except ChatWriteForbiddenError:
                        results.append({"chat_id": cid, "status": "fail",
                                        "error": "Нет прав на запись в этот чат"})
                    except ChannelPrivateError:
                        results.append({"chat_id": cid, "status": "fail",
                                        "error": "Аккаунт не состоит в этом чате"})
                    except Exception as e:
                        results.append({"chat_id": cid, "status": "fail",
                                        "error": f"{type(e).__name__}: {e}"})
            finally:
                await client.disconnect()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return JSONResponse({"results": results})


# ── Client API: отправка от имени аккаунта листенера (по can_write) ──
_SEND_RATE = 20          # сообщений на клиента
_SEND_WINDOW = 60        # за столько секунд
_send_hist: dict = {}


@app.post("/api/v1/send")
async def api_send(
    client: dict = Depends(require_client),
    chat_id: int = Form(...),
    text: str = Form(""),
    reply_to: Optional[int] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Отправить текст и/или файл в один чат. multipart: chat_id, text,
    reply_to (message_id, опц.), file (опц.). Нужен can_write клиента к чату.
    С файлом text идёт подписью (лимит Telegram 1024), без файла — до 4096.
    Ответ: {"chat_id", "message_id"}; 403 нет прав, 429 лимит/FloodWait,
    502 ошибка Telegram."""
    import os
    import tempfile
    import time
    has_file = file is not None and bool(file.filename)
    if not text.strip() and not has_file:
        raise HTTPException(400, "нужен text или file")
    if len(text) > (1024 if has_file else 4096):
        raise HTTPException(400, "text слишком длинный (подпись ≤1024, сообщение ≤4096)")

    acc = (sb.table("tg_client_chat_access").select("can_write")
           .eq("client_id", client["id"]).eq("chat_id", chat_id)
           .eq("can_write", True).execute().data)
    if not acc:
        raise HTTPException(403, "нет права на запись в этот чат")

    now = time.monotonic()
    hist = [t for t in _send_hist.get(client["id"], []) if now - t < _SEND_WINDOW]
    if len(hist) >= _SEND_RATE:
        raise HTTPException(429, f"лимит {_SEND_RATE} сообщений за {_SEND_WINDOW} сек")
    hist.append(now)
    _send_hist[client["id"]] = hist

    from config import TG_API_ID, TG_API_HASH, TG_SESSION_PATH
    try:
        from config import TG_SESSION_SEND_PATH as _SESS
    except ImportError:
        _SESS = TG_SESSION_PATH
    from telethon import TelegramClient
    from telethon.errors import (FloodWaitError, ChatWriteForbiddenError,
                                 ChannelPrivateError)
    from telethon.tl.types import DocumentAttributeFilename

    tmp = None
    if has_file:
        orig_name = os.path.basename(file.filename) or "file"
        fd, tmp = tempfile.mkstemp(prefix="tgsend_", suffix=os.path.splitext(orig_name)[1])
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())
    try:
        async with _send_lock:
            tg = TelegramClient(str(_SESS), TG_API_ID, TG_API_HASH)
            await tg.connect()
            try:
                if not await tg.is_user_authorized():
                    raise HTTPException(503, "send-session не авторизована")
                if tmp:
                    msg = await tg.send_file(
                        chat_id, tmp, caption=text, force_document=True,
                        reply_to=reply_to,
                        attributes=[DocumentAttributeFilename(orig_name)])
                else:
                    msg = await tg.send_message(chat_id, text, reply_to=reply_to)
            except FloodWaitError as e:
                raise HTTPException(429, f"FloodWait: подождите {e.seconds} сек")
            except ChatWriteForbiddenError:
                raise HTTPException(403, "аккаунт не может писать в этот чат")
            except ChannelPrivateError:
                raise HTTPException(403, "аккаунт не состоит в этом чате")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"{type(e).__name__}: {e}")
            finally:
                await tg.disconnect()
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    print(f"  ✉ api/send: client={client.get('name')} chat={chat_id} "
          f"msg={msg.id} file={'yes' if tmp else 'no'}", flush=True)
    return {"chat_id": chat_id, "message_id": msg.id}


# ── clients CRUD (admin pages) ──────────────────────────────────────
@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, _=Depends(require_admin)):
    clients = sb.table("tg_clients").select("*").order("created_at").execute().data or []
    return templates.TemplateResponse(request=request, name="clients.html",
                                      context={"clients": clients})


@app.post("/clients/create")
async def client_create(request: Request, _=Depends(require_admin),
                        name: str = Form(...), note: str = Form("")):
    api_key = f"zpr_{secrets.token_urlsafe(32)}"
    sb.table("tg_clients").insert({
        "name": name, "api_key": api_key, "note": note, "is_active": True,
    }).execute()
    return RedirectResponse("/clients", status_code=303)


@app.post("/clients/{cid}/toggle")
async def client_toggle(cid: str, _=Depends(require_admin)):
    cur = sb.table("tg_clients").select("is_active").eq("id", cid).single().execute().data
    sb.table("tg_clients").update({"is_active": not cur["is_active"]}).eq("id", cid).execute()
    return RedirectResponse("/clients", status_code=303)


@app.post("/clients/{cid}/delete")
async def client_delete(cid: str, _=Depends(require_admin)):
    sb.table("tg_clients").delete().eq("id", cid).execute()
    return RedirectResponse("/clients", status_code=303)


# ── client access (admin page) ──────────────────────────────────────
@app.get("/clients/{cid}/access", response_class=HTMLResponse)
async def client_access_page(cid: str, request: Request, _=Depends(require_admin)):
    client = sb.table("tg_clients").select("*").eq("id", cid).single().execute().data
    chats = sb.table("tg_chats").select("chat_id,title,kind,username").order("title").execute().data or []
    access = sb.table("tg_client_chat_access").select("*").eq("client_id", cid).execute().data or []
    access_map = {a["chat_id"]: a for a in access}
    return templates.TemplateResponse(request=request, name="client_access.html",
                                      context={"client": client, "chats": chats,
                                               "access_map": access_map})


@app.post("/clients/{cid}/access")
async def client_access_save(cid: str, request: Request, _=Depends(require_admin)):
    form = await request.form()
    # Чекбоксы приходят как "read_{chat_id}" и "write_{chat_id}"
    chats = sb.table("tg_chats").select("chat_id").execute().data or []
    rows = []
    for c in chats:
        chat_id = c["chat_id"]
        can_read = form.get(f"read_{chat_id}") == "on"
        can_write = form.get(f"write_{chat_id}") == "on"
        if can_read or can_write:
            rows.append({"client_id": cid, "chat_id": chat_id,
                         "can_read": can_read, "can_write": can_write})
    # Atomic: delete + insert
    sb.table("tg_client_chat_access").delete().eq("client_id", cid).execute()
    if rows:
        sb.table("tg_client_chat_access").insert(rows).execute()
    return RedirectResponse(f"/clients/{cid}/access", status_code=303)


# ── Client API (external apps) ──────────────────────────────────────
@app.get("/api/v1/chats")
async def api_chats(client=Depends(require_client)):
    """Список доступных клиенту чатов."""
    access = (sb.table("tg_client_chat_access")
              .select("chat_id,can_read,can_write")
              .eq("client_id", client["id"]).execute().data or [])
    readable = [a["chat_id"] for a in access if a["can_read"]]
    if not readable:
        return []
    chats = (sb.table("tg_chats")
             .select("chat_id,title,kind,username")
             .in_("chat_id", readable).execute().data or [])
    return chats


@app.get("/api/v1/messages")
async def api_messages(
    client=Depends(require_client),
    chat_id: Optional[int] = Query(None, description="Фильтр по чату"),
    since: Optional[str] = Query(None, description="ISO date, от какой даты"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
):
    """Сообщения из доступных клиенту чатов."""
    access = (sb.table("tg_client_chat_access")
              .select("chat_id").eq("client_id", client["id"])
              .eq("can_read", True).execute().data or [])
    readable = {a["chat_id"] for a in access}
    if not readable:
        return []
    if chat_id is not None:
        if chat_id not in readable:
            raise HTTPException(403, "No read access to this chat")
        q = sb.table("tg_messages").select("*").eq("chat_id", chat_id)
    else:
        q = sb.table("tg_messages").select("*").in_("chat_id", list(readable))
    if since:
        q = q.gte("msg_date", since)
    result = q.order("msg_date", desc=True).limit(limit).offset(offset).execute()
    return result.data or []


@app.get("/api/v1/status")
async def api_status(client=Depends(require_client)):
    """Общий статус синхронизации для клиента."""
    access = (sb.table("tg_client_chat_access")
              .select("chat_id").eq("client_id", client["id"])
              .eq("can_read", True).execute().data or [])
    readable = [a["chat_id"] for a in access]
    total = 0
    if readable:
        total = (sb.table("tg_messages").select("id", count="exact")
                 .in_("chat_id", readable).execute().count or 0)
    return {"chats": len(readable), "messages": total}


# ── search (поиск по тексту сообщений) ───────────────────────────────
@app.get("/api/v1/search")
async def api_search(
    client=Depends(require_client),
    q: Optional[str] = Query(None, min_length=2, description="Подстрока в тексте сообщения И имени вложения"),
    sender: Optional[str] = Query(None, min_length=2, description="Подстрока в имени/username отправителя"),
    chat_id: Optional[int] = Query(None, description="Ограничить одним чатом"),
    since: Optional[str] = Query(None, description="ISO date, от какой даты"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
):
    """Поиск в доступных клиенту чатах. `q` — по `text` + `media_file_name`;
    `sender` — по `sender_name` + `sender_username`. Можно по отдельности или
    вместе (тогда условия по содержимому и отправителю объединяются по AND)."""
    if not q and not sender:
        raise HTTPException(422, "Нужен хотя бы один параметр: q или sender")
    access = (sb.table("tg_client_chat_access")
              .select("chat_id").eq("client_id", client["id"])
              .eq("can_read", True).execute().data or [])
    readable = {a["chat_id"] for a in access}
    if not readable:
        return []
    if chat_id is not None:
        if chat_id not in readable:
            raise HTTPException(403, "No read access to this chat")
        scope = [chat_id]
    else:
        scope = list(readable)

    def _esc(s):  # экранируем спецсимволы LIKE → q/sender трактуются буквально
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    n = limit + offset
    qcols = ["text", "media_file_name"] if q else [None]
    scols = ["sender_name", "sender_username"] if sender else [None]

    def _run(qc, sc):
        qb = sb.table("tg_messages").select("*").in_("chat_id", scope)
        if qc:
            qb = qb.ilike(qc, f"%{_esc(q)}%")
        if sc:
            qb = qb.ilike(sc, f"%{_esc(sender)}%")
        if since:
            qb = qb.gte("msg_date", since)
        return qb.order("msg_date", desc=True).limit(n).execute().data or []

    merged = {}
    for qc in qcols:           # (text|file) × (sender_name|sender_username):
        for sc in scols:        # AND содержимого и отправителя через объединение
            for r in _run(qc, sc):
                merged[(r["chat_id"], r["message_id"])] = r
    rows = sorted(merged.values(), key=lambda r: r.get("msg_date") or "", reverse=True)
    return rows[offset:offset + limit]


# ── media (байты вложения) ───────────────────────────────────────────
def _abs_chat_id(chat_id: int) -> int:
    """Имя каталога хранилища для чата (как в tg_raw_listener)."""
    cid = abs(chat_id); s = str(cid)
    return int(s[3:]) if len(s) >= 13 and s.startswith("100") else cid


@app.get("/api/v1/media")
async def api_media(
    client=Depends(require_client),
    chat_id: int = Query(..., description="ID чата"),
    message_id: int = Query(..., description="ID сообщения в чате"),
):
    """Отдаёт байты вложения сообщения. Доступ — по can_read клиента к чату.
    Файл ищется по media_path, а если он пуст/неверен — по chat_id+message_id
    в хранилище (media_path в БД заполнен не у всех записей)."""
    acc = (sb.table("tg_client_chat_access")
           .select("can_read").eq("client_id", client["id"])
           .eq("chat_id", chat_id).eq("can_read", True).execute().data)
    if not acc:
        raise HTTPException(403, "No read access to this chat")
    row = (sb.table("tg_messages")
           .select("*")
           .eq("chat_id", chat_id).eq("message_id", message_id)
           .limit(1).execute().data)
    if not row:
        raise HTTPException(404, "Message not found")
    m = row[0]
    if not m.get("has_media"):
        raise HTTPException(404, "No media in this message")
    base = Path(STORAGE_DIR).resolve()
    full = None
    if m.get("media_path"):
        cand = (base / m["media_path"]).resolve()
        if cand.is_relative_to(base) and cand.is_file():
            full = cand
    if full is None:  # fallback: ищем файл по chat-каталогу и message_id
        for cand in base.glob(f"TELEGRAM/*/*/{_abs_chat_id(chat_id)}/{message_id}_*"):
            if cand.is_file():
                full = cand.resolve(); break
    if full is None or not full.is_relative_to(base):
        from archive_manifest import archived
        arc = archived(_abs_chat_id(chat_id), message_id)
        if arc:
            raise HTTPException(410, f"Файл перенесён в архив (Dropbox), месяц {arc['month']}; "
                                     f"на сервере хранятся последние 4 месяца")
        raise HTTPException(404, "Media file not on disk (not downloaded yet)")
    mime = m.get("media_mime") or mimetypes.guess_type(str(full))[0] or "application/octet-stream"
    # Сигнал: отдаём оригинал или сжатый образ (proxy). media_is_proxy появится
    # после миграции; пока колонки нет → .get() вернёт None → "false" (оригинал).
    hdrs = {"X-Media-Proxy": "true" if m.get("media_is_proxy") else "false"}
    if m.get("media_original_size"):
        hdrs["X-Media-Original-Size"] = str(m["media_original_size"])
    return FileResponse(str(full), media_type=mime,
                        filename=m.get("media_file_name") or full.name,
                        headers=hdrs)


# ── health ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "ts": _now_iso()}


# ── main ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("tg_web_admin:app", host="0.0.0.0", port=8080, reload=True)
