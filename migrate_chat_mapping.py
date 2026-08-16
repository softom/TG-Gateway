"""Однократная миграция: маппинг чат->объект из локального tg_chats.object_id
в облачный tg_chats.object_id (plain uuid, без FK). Запуск: conda run -n zpr python migrate_chat_mapping.py"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from supabase import create_client
from config import (
    SUPABASE_URL, SUPABASE_SECRET_KEY,
    SUPABASE_CLOUD_URL, SUPABASE_CLOUD_SECRET_KEY,
)

local = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
cloud = create_client(SUPABASE_CLOUD_URL, SUPABASE_CLOUD_SECRET_KEY)

rows = (
    local.table("tg_chats")
    .select("chat_id,object_id,title")
    .not_.is_("object_id", "null")
    .execute()
    .data
) or []
print(f"Локальных привязанных чатов: {len(rows)}")

# code объектов для читаемого вывода
objs = local.table("objects").select("id,code").execute().data or []
code_by_id = {o["id"]: o["code"] for o in objs}

ok = 0
for r in rows:
    try:
        cloud.table("tg_chats").upsert({
            "chat_id": r["chat_id"],
            "object_id": r["object_id"],
            "is_whitelisted": True,
            "handler": "raw",
            "title": r.get("title"),
        }, on_conflict="chat_id").execute()
        print(f"  ✓ {r['chat_id']} -> {code_by_id.get(r['object_id'], r['object_id'])}  «{r.get('title')}»")
        ok += 1
    except Exception as e:
        print(f"  ✗ {r['chat_id']}: {e}")

v = cloud.table("tg_chats").select("chat_id", count="exact").not_.is_("object_id", "null").execute()
print(f"\nПеренесено: {ok}. В облаке привязанных чатов: {v.count}")
