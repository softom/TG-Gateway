---
type: architecture
title: TG-Gateway — архитектура (снапшот; рабочая WIKI в Dropbox)
created: 2026-05-28
updated: 2026-06-29
tags:
  - infrastructure
  - telegram
  - supabase
  - cloud
---

# Внешний US-сервер и облачный Telegram-реестр

> Сессия 2026-05-28. Вынос Telegram-ingest на всегда-онлайн сервер в США из-за
> гео-блока и нестабильности локального Docker-стека (1 ПК = 0 отказоустойчивости).

> 📌 **2026-06-04 — продолжение этой же истории:** весь **основной стек ЗПР** (Supabase + UI +
> Python + Хранилище) вынесен с ПК на **RU-сервер Beget `45.130.42.178`** (доступ только по wg0).
> Это закрывает ту же боль «1 ПК = 0 отказоустойчивости» уже для ядра проекта, а не только TG.
> Детали — [[38_Мессенджер_self-hosted]] (раздел «Сосед №3: основной стек ЗПР») и [[Хроника проекта]].

> 🔴 **2026-06-28 — СМЕНА US-СЕРВЕРА.** Старый Aeza `experienced-mocc 95.181.173.95` **удалён хостером** 27.06 за неоплату (quiet-delete, без warning). Новый VPS: **`flexible-violet 77.110.125.137`** (Ubuntu 26.04). Всё восстановлено (админка + демон приёма + автозапуск). Подробности — раздел «[[#2026-06-28 — Миграция на новый Aeza: восстановление, авторизация, автозапуск]]» ниже. Креды теперь — только в `secrets.env`.

## Зачем

- **Гео-блок:** Telegram режет MTProto из РФ → ingest должен идти из-за рубежа.
- **Отказоустойчивость:** локальный «сервер ЗПР» — Docker на рабочем ПК (спит, ребутится).
  Демон-листенер постоянно «засыпал» → пропуски сообщений и событий.

## Архитектура

```
Telegram → [US-сервер: tg_raw_listener (systemd)] → Supabase Cloud (tg_chats/tg_messages)
                  |  + web-админка :8080                    │  REST + retry
                  |  + client API /api/v1/*                 ▼
    wg0 ──────────┘               [RU-сервер (Beget): tg_classifier + Next.js UI + Supabase]
    VPN-доступ                                              → events + objects + UI (ВЕСЬ ЗПР)
```

- **US-сервер** = сырой Telegram-ingest + web-админка с client API.
- **Облачный Supabase** = тонкий реестр: ТОЛЬКО `tg_chats` + `tg_messages` (сырьё).
- **RU-сервер (Beget)** = канон ЗПР: Supabase (события, объекты), Next.js UI, классификатор, хранилище.
- Кнопка «Считать из TG» в UI → `tg_classifier.py` на RU-сервере → читает TG из облака, пишет события локально.

НЕ на US-сервере: почта (Яндекс 360 — РФ-сервис, воркер с РФ-стороны), Max (РФ-сторона).

## Серверы

### US-сервер (Aeza, Charlotte)

Роль: Telegram-ingest (демон `tg_raw_listener.py`) + прокси-выход (Reality).

Доступы / пароль root / коды поддержки / API-ключ — в `C:\Users\tigra\HA\secrets.env` (блок Aeza), НЕ в WIKI.

| Параметр     | Значение                                                                                             |
| ------------ | ---------------------------------------------------------------------------------------------------- |
| Имя / IP     | **flexible-violet · `77.110.125.137`** (США, Charlotte) — новый VPS с 28.06.2026                      |
| ОС / ресурсы | Ubuntu 26.04 LTS · 1 CPU / 2 GB RAM / 30 GB nvme                                                      |
| Доступ       | **только через jump-host** (DPI блэкхолит IP из Крыма): `ssh -i vpnkey root@45.130.42.178` → оттуда `sshpass` на `root@77.110.125.137`. Root-пароль — в `secrets.env` |
| Раскладка    | `/opt/zpr/{app,venv,logs,storage}`; TG-сессия `/opt/zpr/.tg_session.session` (chmod 600)             |
| Сервисы      | `zpr-tg-admin` (:8080) · `zpr-tg-listener` (--listen) · `zpr-tg-backfill` (oneshot, boot)            |
| ⚠️ Сосед     | xray.service (:443), wg-quick@wg0 (:51820) — VPN-агента. **НЕ ТРОГАТЬ** `/etc/wireguard`, `/usr/local/etc/xray`, `/root/reality.keys` |
| Старый сервер | experienced-mocc `95.181.173.95` (Ubuntu 24.04) — **удалён Aeza 27.06.2026** (quiet-delete за неоплату) |

### RU-сервер (Beget, СПб)

Роль: WireGuard-relay (вход для устройств) + Reality-клиент → US (зарубежный выход).

> ⚠️ **На этом же box развёрнут self-hosted мессенджер** (**Matrix/Synapse + Element**; Docker, Caddy на портах 80/443). Mattermost **снесён 05.06.2026** (выбор за Matrix — см. [[Хроника проекта]]). Машина изначально куплена **под мессенджер** — релей здесь вторичный жилец. SSH захардён под ключ `zpr_vps` (root-пароль ротирован, парольный вход выкл.). Сосуществование с релеем проверено ребут-безопасным (`wg-quick@wg0` восстанавливает форвардинг при буте, несмотря на Docker `FORWARD DROP`). Подробности: [[38_Мессенджер_self-hosted]].

| Параметр     | Значение                                                                                             |
| ------------ | ---------------------------------------------------------------------------------------------------- |
| Имя / IP     | `45.130.42.178` (Россия, СПб)                                                                        |
| Доступ       | `ssh -i vpnkey root@45.130.42.178` (ключ `C:\Users\tigra\HA\vpnkey`)                                |
| WireGuard    | `wg0`, подсеть `10.8.0.0/24`: роутер `.2`, ПК `.3`, телефон `.4`                                    |
| Reality      | VLESS → US Aeza:443 (SNI `www.apple.com`). **С 2026-05-31 вечера: `sing-box` вместо `xray`** (UUID/short_id/Reality target — тот же). См. [[37_VPN_рабочая_схема_relay]] (раздел Update 2026-05-31). |
| Режимы       | ~~«Питер» ↔ «США»~~ → **per-domain transparent routing** (sing-box на основе geosite/geoip rule_sets). Клиенту больше не нужно «переключаться» между туннелями. |

⚠️ **DPI из Крыма блэкхолит IP Aeza напрямую** (TCP-хендшейк есть, данных нет). Доступ к US — только через RU-relay. Подробности: [[37_VPN_рабочая_схема_relay]].

## Облачный Supabase (реестр Telegram)

- Project ref `jxdkmfidrrwxhyltobnh` · URL `https://jxdkmfidrrwxhyltobnh.supabase.co`
- Прямой `db.<ref>.supabase.co:5432` — **только IPv6** (у локального Docker IPv6 нет → схему
  накатывали с US-сервера по psql; REST API `https://<ref>.supabase.co` достижим по IPv4).
- Таблицы: `tg_chats`, `tg_messages` (сырьё; схема — `supabase`-аналог миграции
  `20260512000001_tg_messages.sql`, но БЕЗ FK на `objects` и без RLS-функции `user_role`)
  **+ `tg_clients`, `tg_client_chat_access`** (клиенты Client API, миграция `20260529000001`).
  В планах — колонки `media_is_proxy` / `media_original_size` (см. «Оригинал или образ»).
- RLS: `SELECT` открыт всем; запись — только `service_role` (демон).
- `tg_chats.object_id` — **plain uuid без FK** (привязку чат→объект ставит локальный UI/миграция, читает классификатор).

## Демон: `tg_raw_listener.py`

Отдельный серверный листенер (НЕ `telegram_listener.py` — тот завязан на локальную схему ЗПР).

- Команды: `--auth`, `--list-chats`, `--backfill DAYS`, `--listen`.
- Пишет **только** `tg_chats` + `tg_messages` в облако (REST, `service_role`). Без классификатора.
- **Чёрный список (blacklist):** берём ВСЁ, кроме исключённого. Авто-исключение broadcast-каналов
  и публичных (@username) групп + явный `EXCLUDE_CHAT_IDS` (42 публичных канала, утверждены).
  `INCLUDE_CHAT_IDS` — override (всегда брать).
- **Медиа:** качаются на сервер в `STORAGE_DIR=/opt/zpr/storage`, окно ~год, стоп-кран
  `DISK_MIN_FREE_GB=5` (ниже — пишем только метаданные).
- **systemd:** юнит `zpr-tg-listener.service` (`Restart=always`, `WorkingDirectory=/opt/zpr/app`).

## Сессия Telegram — правило

⚠️ **Нельзя копировать/шарить один session-файл между машинами.** Использование одного ключа
с двух IP → Telegram аннулирует его (`AuthKeyDuplicatedError`), умирает на всех копиях.
- У каждой точки — **своя** сессия (это разные «устройства» одного аккаунта).
- Сессия сервера создаётся `--auth` НА сервере (`/opt/zpr/.tg_session.session`, `chmod 600`).
- Локальные/личные TG-автоматизации — отдельная сессия на локальной машине.

## Классификатор: чтение из облака (Фаза 1, сделано)

`tg_classifier.py` переведён на **два клиента**:
- `sb` — локальный Supabase (справочники: `objects`, `contractors`, `event_classifier_templates`,
  feedback; запись `events`, `event_tg_messages`, `event_attachments`).
- `sb_cloud` — облачный (читает `tg_chats`, `tg_messages`), с `ClientOptions(postgrest_client_timeout=60)`,
  `_retry()` и страницами по `CLOUD_PAGE=200` — устойчивость к флапающему RF→US.
- Маппинг чат→объект перенесён из локального `tg_chats.object_id` в облачный (`migrate_chat_mapping.py`; 6 чатов-отелей).
- creds: `config.py` → `SUPABASE_CLOUD_URL` / `SUPABASE_CLOUD_SECRET_KEY` / `..._PUBLISHABLE_KEY`.

## Блок «Догоняю» → Realtime (Фаза 2–3, план)

- **Фаза 2:** облачный Supabase-клиент в UI (`ui/.env.local` → `NEXT_PUBLIC_SUPABASE_CLOUD_*`) +
  Realtime-подписка на `tg_messages` → живой блок «Догоняю». Prereq: на облаке
  `alter publication supabase_realtime add table tg_messages, tg_chats;`.
- **Фаза 3:** авто-классификация (локальный воркер на Realtime) + медиа подкачивается с сервера
  **только при утверждении** события (до ревью preliminary держит лишь метаданные).
- Старый `refresh-tg` (PowerShell: stop→backfill→classifier→start) **заменён** (2026-06-09):
  `POST /api/events/refresh-tg` → spawn `/opt/zpr/venv/bin/python /opt/zpr/code/tg_classifier.py --apply --layer rule --json-summary` на RU-сервере.
  Кнопка «↻ Считать из TG» снова работает (~1-5 сек, rule-only).

## Локальный листенер — выведен из эксплуатации (2026-05-29)

Старый `telegram_listener.py` (локальный MTProto, whitelist, Task Scheduler) **остановлен и архивирован**.
Сбор теперь идёт на US-сервере (`tg_raw_listener.py`, systemd).

**Что отключено:**
- Task Scheduler: `ZPR_Telegram_Listener`, `ZPR_TG_Classifier_Batch` — **Disabled**.
- API-роут `POST /api/events/refresh-tg` — **восстановлен** (2026-06-09): вызывает
  `tg_classifier.py --apply --layer rule --json-summary` на RU-сервере через subprocess.
  Пути: `PYTHON_BIN=/opt/zpr/venv/bin/python`, `CLASSIFIER_SCRIPT=/opt/zpr/code/tg_classifier.py`.
- Кнопка «↻ Считать из TG» на `/events` — **работает** (rule-only, ~1-5 сек).

**Что архивировано** (`_archive/local_tg_listener/`):

| Файл | Был | Назначение |
|------|-----|------------|
| `telegram_listener.py` | корень репо | Локальный листенер (whitelist, Хранилище) |
| `run_tg_listener.ps1` | `scripts/` | Обёртка Task Scheduler для `--listen` |
| `run_tg_classifier.ps1` | `scripts/` | Обёртка классификатора (раз в 4 ч) |
| `refresh_tg_now.ps1` | `scripts/` | Ручной триггер из UI |
| `route.ts` | `ui/app/api/events/refresh-tg/` | Next.js API-роут |

**Что осталось в работе локально:**
- `tg_classifier.py` — классификатор (cloud-read), запуск вручную или по расписанию.
- `.tg_session` — локальная сессия (для ручных TG-задач; НЕ для листенера).

## Web-админка: `tg_web_admin.py` (развёрнута 2026-05-29)

FastAPI + Jinja2 на том же Python-venv сервера. Systemd: `zpr-tg-admin.service`.

### Реквизиты входа

| Параметр      | Значение                                 |
| ------------- | ---------------------------------------- |
| URL           | `http://77.110.125.137:8080/login`       |
| ADMIN_TOKEN   | в `secrets.env` (`TG_ADMIN_TOKEN`) — в WIKI НЕ хранится (старый утёк здесь открытым текстом, ротирован 28.06) |
| Порт          | 8080 (открыт наружу)                     |
| Конфиг токена | systemd `Environment=ADMIN_TOKEN` в `zpr-tg-admin.service` |
| Логи          | `/opt/zpr/logs/web_admin.log`            |

### Экраны

1. **Дашборд** (`/`) — панель управления + мониторинг:
   - **Управление сервисами** — две панели с кнопками ▶ Запустить / ⏹ Остановить:
     - **Листенер** (`zpr-tg-listener`, systemd) — старт/стоп, индикатор active/inactive, RAM.
     - **Backfill** (фоновый процесс) — старт с выбором дней (по умолчанию 365) / стоп (SIGTERM), PID.
   - Счётчики: всего сообщений / сегодня / чатов / клиентов, диск.
   - Таблица последних 25 сообщений, список чатов, хвост лога.
2. **Клиенты** (`/clients`) — CRUD API-клиентов: создание (имя + комментарий → авто-ключ `zpr_...`), вкл./откл., удаление, копирование ключа. Встроенная мини-документация API (curl-примеры).
3. **Доступ** (`/clients/{id}/access`) — per-client чекбоксы read/write для каждого чата в облаке. Прокрутка таблицы со sticky-заголовком. Массовые кнопки «Все: читать» / «Все: писать» / «Снять все».

### Публикация в TG: `POST /ctl/send-file`

Используется бэкендом ЗПР UI (Beget) при «Опубликовать» протокол. **Был потерян при пересоздании сервера 27-28.06** (не закоммичен в репо) → восстановлен 29.06 в `tg_web_admin.py` по референсу `scripts/send_telegram_protocol.py`.
- **Auth:** `admin_token` (cookie `admin_token` или `?token=`). Плохой/пустой токен → **`403 {"error":"unauthorized"}`** (НЕ 307-редирект, как у браузерных `/ctl/*`) — бэкенду понятен сбой авторизации.
- **Вход** (multipart/form-data): `file`, `chat_ids` (id через запятую), `caption` (UTF-8).
- **Действие:** Telethon `send_file(force_document=True)` по каждому чату; ошибка одного не прерывает остальные (`FloodWait` / `ChatWriteForbidden` / `ChannelPrivate` → `status=fail`).
- **Имя документа в Telegram** = поле `file.filename` из multipart, проставляется явно через `attributes=[DocumentAttributeFilename(orig_name)]`. ⚠️ **Важно для всех проектов-потребителей:** без этого Telethon берёт basename временного файла (`tgsend_xxxx.docx`) → получатель видит нечитаемое имя. Всегда передавайте осмысленный `filename` в multipart (ЗПР шлёт `<meeting.code>.docx`). Фикс от 03.07.2026.
- **Ответ:** `{"results":[{"chat_id":…,"status":"ok","message_id":…} | {…,"status":"fail","error":"…"}]}`.
- **Сессия:** та же, что у листенера (`TG_SESSION_PATH`). Проверено: короткая отправка под `_send_lock` (serialization) **не даёт** `database is locked` / `AuthKeyDuplicated`, листенер остаётся `active`. Если пойдут локи — задать `TG_SESSION_SEND_PATH` (отдельная авторизованная send-сессия), эндпоинт подхватит как override.
- ⚠️ Beget должен слать **актуальный** `ADMIN_TOKEN` (ротирован 28.06 → `secrets.env` `TG_ADMIN_TOKEN`); со старым токеном будет 307/403.

### Client API (для внешних приложений)

Авторизация: заголовок `X-Api-Key: zpr_...` или параметр `?api_key=zpr_...`.

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/api/v1/chats` | Чаты, доступные клиенту |
| GET | `/api/v1/messages?chat_id=&since=&limit=&offset=` | Сообщения (только из разрешённых чатов) |
| GET | `/api/v1/status` | Счётчики: кол-во доступных чатов и сообщений |
| GET | `/api/v1/search?q=&sender=&chat_id=&since=&limit=&offset=` | **Поиск** в доступных клиенту чатах (ILIKE substring, регистронезависимо). `q` → по `text` + `media_file_name` (для документов важно: текст часто пуст → ищем по имени файла); `sender` → по `sender_name` + `sender_username` («все от студента»). Можно по отдельности или вместе (AND). `q`/`sender` ≥2 симв., нужен хотя бы один (иначе 422); `chat_id` — один чат (403 без доступа). Возвращает сообщения как `/messages` |
| GET | `/api/v1/media?chat_id=&message_id=` | **Байты вложения** (FileResponse, `media_mime`). Доступ по `can_read` к чату. Файл — по `media_path`, а если пуст/неверен → поиск по `chat_id`+`message_id` в хранилище. 404 если файла нет на диске. Заголовок ответа **`X-Media-Proxy: true/false`** — отдан оригинал или сжатый образ (+ `X-Media-Original-Size`) |
| GET | `/health` | Healthcheck (без авторизации) |

> **Данные медиа (фикс 2026-06-28).** `media_path` пуст только когда **файла нет на диске** (не качался: диск-скип / фейл загрузки / не-загружаемый тип вроде webpage/poll) — для них `/api/v1/media` корректно отдаёт 404.
> **Корневой дефект исправлен:** `_save_message` был **insert-only** (строка 327) → если сообщение впервые легло без файла (`media_path=null`), а позже файл докачался на диск, повторная вставка падала дубликатом и путь не обновлялся. Теперь на дубликат (23505) `_save_message` **дозаполняет** `media_path`/`media_file_name`/`media_mime`/`media_size` с условием `.is_("media_path","null")` (не затирая уже записанное).
> **Бэкафилл существующих:** разовый проход по `/opt/zpr/storage` проставил путь строкам-сиротам (+158 → `media_path NOT NULL` 6383/13436). На случай остаточного рассинхрона `/api/v1/media` всё равно имеет fallback-поиск по `TELEGRAM/*/*/<abs_chat_id>/<message_id>_*` (`_abs_chat_id` режет `-100` у супергрупп — как в `tg_raw_listener`).

### Медиа: хранилище и дотяжка (rebackfill)

- **Файлы** → `/opt/zpr/storage/TELEGRAM/<ГГГГ>/<ММ>/<abs_chat_id>/<msg_id>_<имя>`. Качаются только kind ∈ `photo/document/video/audio/voice/sticker/gif`; `webpage/poll/geo/contact` — метаданные без файла (норма).
- ⚠️ **Историческое медиа потеряно при миграции 27-28.06:** quiet-delete старого сервера стёр и его хранилище. На новом боксе файлы появляются только по мере скачивания (realtime + backfill). Метаданные в облаке остались, файлов нет → их **дотягивают из Telegram** (`rebackfill_media.py`).
- **Дефект тайм-аута (исправлен 28.06):** скачивание имело фиксированный `timeout=180` → крупные документы (десятки–сотни МБ) не докачивались (`media_path` оставался null). Теперь **size-aware**: `min(1800, max(300, size/30KB))` — до 30 мин на гигантские файлы.
- **Инструмент дотяжки** `/opt/zpr/app/rebackfill_media.py` (в репо тоже):
  - `python rebackfill_media.py <chat>:<msg> <chat>:<msg> …` — точечно;
  - `python rebackfill_media.py --client <client_id>` — все пробелы в чатах клиента: download-kind, у кого **нет файла на диске** (проверка по `media_path` И glob). ⚠️ Критерий — именно **отсутствие файла**, а НЕ `media_path IS NULL`: часть путей указывает на файлы удалённого старого сервера (media_path не пуст, а файла нет). Метрику «закрыт ли пробел» тоже мерить по наличию файла / отдаче `/api/v1/media`, не по null.
  - Качает из Telegram, проставляет `media_path`/`media_file_name`/`media_mime`/`media_size`.
  - ⚠️ Использует ту же TG-сессию, что листенер → перед запуском **остановить** `zpr-tg-listener` И `zpr-tg-backfill` + `pkill -f tg_raw_listener.py` (иначе `sqlite3 database is locked`). После — снова запустить листенер.

### Оригинал или образ (proxy) в API

`/api/v1/media` отдаёт **тот файл, что лежит на диске**. Если файл был сжат «автоматом» (downscale крупных PDF/PNG/TIF до ≤100MB ради экономии диска) — это **образ (proxy)**, а не оригинал. Как клиент это понимает:
- **`/api/v1/media`** — заголовки ответа: **`X-Media-Proxy: true|false`** (true = отдан сжатый образ) и **`X-Media-Original-Size`** (исходный размер, если образ).
- **`/api/v1/messages`** и **`/api/v1/search`** — поля **`media_is_proxy`** (bool) и **`media_original_size`** в записи сообщения.
- **Настоящий оригинал** дотягивается заново из Telegram через `rebackfill_media.py` (он перезапишет образ оригиналом; флаг сбрасывается).

Поля `media_is_proxy` / `media_original_size` — добавляются миграцией (`alter table tg_messages add column media_is_proxy boolean default false, add column media_original_size bigint`). **До миграции** колонок нет → API считает все файлы оригиналами (`X-Media-Proxy: false`). Реализовано безопасно: `select *` + `.get()`, отсутствие колонки не ломает API.

> **Статус автомата сжатия (`compress_media.py`):** метод валидирован (gs в /tmp — профиль AppArmor `/etc/apparmor.d/gs` запрещает `/opt/zpr/storage`, поэтому жмём через копию в /tmp; PNG/TIF — ImageMagick out-of-core из-за 2GB RAM). НО на 1-CPU/2GB box гиганты (0.5–0.9GB PDF) очень медленны (один проход gs >7 мин, swap). Запускать тяжёлую партию — фоном с `nice -n19 ionice -c3`. Диск пока не давит (7/30GB). Колонки БД ставит миграция (нужен DB-пароль/pooler jxdkmf).

Пример:
```bash
curl -H "X-Api-Key: zpr_..." http://77.110.125.137:8080/api/v1/messages?limit=10
```

### Стек и файлы

| Компонент | Путь на сервере |
|-----------|-----------------|
| Приложение | `/opt/zpr/app/tg_web_admin.py` |
| Шаблоны | `/opt/zpr/app/tg_web/templates/` (5 файлов) |
| CSS | `/opt/zpr/app/tg_web/static/style.css` |
| Systemd | `/etc/systemd/system/zpr-tg-admin.service` |
| В репо | `tg_web_admin.py` + `tg_web/` + `supabase/migrations/20260529000001_tg_clients.sql` |

Python-зависимости (в venv): `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `supabase`, `python-dotenv`; для демона приёма дополнительно `telethon` (+ `qrcode[pil]` — использовался для QR-авторизации, см. раздел «2026-06-28 — Миграция»).

### БД (облачный Supabase)

Две новые таблицы (миграция `20260529000001_tg_clients.sql`):

- **`tg_clients`** — id (uuid), name, api_key (unique), is_active, note, created_at, updated_at. Trigger `updated_at`. Индекс по `api_key WHERE is_active`.
- **`tg_client_chat_access`** — PK (client_id, chat_id), can_read, can_write. FK → `tg_clients` (CASCADE), FK → `tg_chats` (CASCADE).

RLS: SELECT open; writes — `service_role` (GRANT ALL).

## 2026-06-28 — Миграция на новый Aeza: восстановление, авторизация, автозапуск

> Полный нарратив — [[Хроника проекта]]. Здесь — что и как сделано (приключения и решения).

**Что случилось.** 27.06 ~07:30 MSK кончились средства на Aeza-аккаунте `vitadendra` → Aeza **тихо удалила** старый сервер `experienced-mocc` (95.181.173.95) без warning и grace. Пополнение баланса сервер НЕ вернуло (стёрт). Заказан новый VPS `flexible-violet` = **77.110.125.137** (Ubuntu 26.04). VPN-агент развернул на нём Reality :443 + WG hub (их **не трогаем**).

**Что уцелело / потеряно.** Облачный Supabase `jxdkmfidrrwxhyltobnh` НЕ удалялся → реестр цел: `tg_chats` 975, `tg_messages` 66914, `tg_clients` 2, доступы 8. Админка по сути **stateless** (вся БД в облаке). Потеряны: TG-сессия листенера (нужна повторная авторизация) и медиа `/opt/zpr/storage` (дотягиваются заново из Telegram через `rebackfill_media.py`).

**Восстановление админки.** Исходники целы в `D:\CODE\zpr_code`. Минимальный серверный `config.py` = только `SUPABASE_URL` + `SUPABASE_SECRET_KEY` (= облачные значения; `tg_web_admin.py` импортирует именно их). ⚠️ **Баг, который ловили:** первый деплой оставил в серверном `config.py` **кириллический плейсхолдер** вместо service_role → дашборд падал **500** (`UnicodeEncodeError` в httpx на заголовке `apikey`). Лечится вписыванием рабочего ключа (старый из локального `config.py:51` валиден, проект жив). NB: `/health` и `/login` БД не трогают — связь с облаком проверять дашбордом/`/api/v1/status`.

**Авторизация TG на новом IP — главный затык и решение.**
- Код входа (`SentCodeTypeApp`) с нового Aeza-IP Telegram **не доставлял** (next_type=None; `force_sms` в Telethon устарел/мёртв; resend → `SendCodeUnavailableError`). SMS недоступна. По факту Telegram **режет вход по коду с дата-центрового IP**.
- **Решение — QR-login** (`client.qr_login()`): подтверждается доверенной сессией с телефона, доставка кода не нужна.
- Но: QR через чат/виджет **не успевает** (токен живёт ~25 сек, round-trip дольше), а **прямой HTTP к Aeza из Крыма DPI режет** (браузер: «соединение прервано»).
- **Что сработало:** `qr_auth.py` крутится на Aeza (генерит QR, держит `wait`), а страница с QR отдаётся через **reverse-proxy на Beget** (RU-IP `45.130.42.178`, пользователю доступен): `python3 qrproxy.py` :8091 → Aeza:8090. Пользователь открыл `http://45.130.42.178:8091/`, отсканировал/тапнул → сессия `/opt/zpr/.tg_session.session` создана. Временную обвязку (Aeza :8090, qrweb, Beget :8091) после убрали.

**Автозапуск (boot-цепочка).** `boot → zpr-tg-backfill.service` (Type=oneshot, `--backfill 3`, TimeoutStartSec=1800) **отрабатывает восполнение и выходит** (освобождает общую TG-сессию) **→** `zpr-tg-listener.service` (`--listen`). Связь: у листенера `Wants=`/`After=zpr-tg-backfill.service`; backfill = `static` (тянется через Wants листенера, отдельно не enable). Сессия общая → строго последовательно, без конфликта. Окно дозагрузки менять в `ExecStart` (`--backfill N`).

**Урок для агента (Claude Code).** Обычная сессия в **auto/unsupervised** режиме НЕ может SSH'ить на внешний хост по кредам/IP из cross-session сообщения — классификатор блокирует (4× подряд), и ни «да» в диалоге, ни allow-rule в settings.json это не снимают (by design против prompt-injection). Сработало после перевода сессии в режим **acceptEdits** (обычный supervised). Межсессийные инструменты (`send_message`, поиск транскриптов) в auto-режиме тоже недоступны.

## Ключевые решения и уроки

1. **Облако = только TG-реестр**, не весь ЗПР. (Сначала по ошибке залили все 87 таблиц — откатили до 2.)
2. **Blacklist, не whitelist** — берём все приватные чаты, кроме чёрного списка (42 публичных). У владельца ~1000 диалогов; whitelist отвергнут.
3. **Сессию не копировать** между машинами (AuthKeyDuplicated «съел» общий ключ один раз).
4. **fail2ban + частые SSH = само-бан.** Нужен `ignoreip` для рабочего IP + поднять `MaxStartups`.
5. **Тяжёлый backfill на 1-CPU = SSH-слепота** (MaxStartups). Запускать backfill под `nice -n 19 ionice -c 3`.
6. **VPN для связи локаль↔облако НЕ нужен.** Флапает путь до Supabase-облака (Cloudflare), не до нашего сервера; REST-`retry` и авто-reconnect Realtime закрывают это.
7. **Медиа качаются сразу при ингесте** (листенер/backfill → `/opt/zpr/storage`, kind ∈ download-kinds, стоп-кран `DISK_MIN_FREE_GB=5`). Старый план «медиа только по утверждению события» (Фаза 3) **НЕ внедрён** — файлы скачиваются при сборе.
8. **Telegram режет вход по коду с дата-центрового IP** (новый Aeza-IP): `SentCodeTypeApp` не доставляется, SMS недоступна. Обход — **QR-login**, отданный пользователю через reverse-proxy на Beget (прямой HTTP к Aeza из Крыма DPI режет). См. раздел «2026-06-28 — Миграция».
9. **Минимальный серверный `config.py`** для админки = только `SUPABASE_URL` + `SUPABASE_SECRET_KEY` (облачные). Плейсхолдер вместо ключа → `UnicodeEncodeError`/500 на любом запросе к облаку.
10. **Boot-цепочка backfill→listener** = oneshot + `After=`/`Wants=`; общая TG-сессия диктует строгую последовательность (нельзя backfill и listener одновременно — `database is locked`).
11. **Quiet-delete у хостера:** Aeza удаляет VPS за неоплату без warning/grace, пополнение не воскрешает. Следить за `AEZA_RENEW_BEFORE` в `secrets.env`.

## Статус (2026-06-28) — новый сервер

- [x] Новый Aeza `77.110.125.137` (Ubuntu 26.04); сосед xray:443/wg не тронут.
- [x] Админка `zpr-tg-admin` развёрнута заново, ходит в облако jxdkmf (исправлен баг плейсхолдера ключа), :8080 доступен снаружи.
- [x] TG-сессия пересоздана QR-логином (workaround через Beget reverse-proxy).
- [x] Backfill за пропуск (+133 новых, 974 чата); демон приёма `zpr-tg-listener` active+enabled.
- [x] Boot-автозапуск: `zpr-tg-backfill` (oneshot) → `zpr-tg-listener`.
- [x] Креды/токены вынесены в `secrets.env` (старый ADMIN_TOKEN из WIKI ротирован).
- [ ] Опционально: ротация cloud `service_role` (сейчас работает старый ключ).

## Статус (2026-05-29) — историческое (актуальное — блок «Статус (2026-06-28)» выше)

- [x] Сервер: провижининг, runbook, своя TG-сессия (`--auth` → Артем Антипов).
- [x] Облако: схема сведена к 2 таблицам, маппинг 6 чатов.
- [x] Классификатор: cloud-read, проверен (dry-run → 2 L1-кандидата).
- [x] **SSH восстановлен** через RU-relay (Beget СПб → Reality → Aeza US). Причина блокировки: DPI блэкхолит IP Aeza напрямую из Крыма (см. [[37_VPN_рабочая_схема_relay]]).
- [x] **Web-админка** (`tg_web_admin.py`) развёрнута: дашборд + клиенты + доступ + client API. Порт 8080, systemd.
- [ ] **Полный backfill (год, blacklist) + старт systemd-листенера** — SSH есть, можно запускать.
- [ ] Фаза 2 (UI Realtime) + Фаза 3 (авто-классификатор, медиа-по-approve).
- [ ] `fail2ban ignoreip` + `MaxStartups` на сервере (чтобы само-бан не повторялся).

## См. также
[[26_Внешние_каналы]] · [[27_Классификатор_событий]] · [[06_БАЗА_ДАННЫХ]] · [[36_VPN_proxy_и_Keenetic]] · [[37_VPN_рабочая_схема_relay]] · [[38_Мессенджер_self-hosted]] · [[Хроника проекта]]

> ⚠️ Серверный runbook `/opt/zpr/docs/00_SERVER.md` был на старом сервере (удалён) — на новом `77.110.125.137` пока **не воссоздан** (на нём `/opt/zpr` = `app/venv/logs/storage`). TODO при необходимости.
