import json
import time
import base64
import urllib.request
import urllib.error
import urllib.parse
from app.core.database import get_setting

def _get_supabase_base():
    url = (get_setting("db_url") or get_setting("db1_url") or "").strip()
    key = (get_setting("db_key") or get_setting("db1_key") or "").strip()
    if not url or not key:
        return None, None
    url = url.rstrip("/")
    if "/rest/v1" in url:
        base = url.split("/rest/v1")[0] + "/rest/v1"
    else:
        base = url + "/rest/v1"
    return base, key

def _post_rows(table: str, rows: list[dict], upsert: bool = False) -> bool:
    base, key = _get_supabase_base()
    if not base or not key:
        return False
    endpoint = f"{base}/{table}"
    data = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    prefer = "return=minimal"
    if upsert:
        prefer = f"{prefer},resolution=merge-duplicates"
    req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        print(f"Supabase insert error: {e.code} {body}")
        return False
    except Exception as e:
        print(f"Supabase insert error: {e}")
        return False

def _get_rows(table: str, params: dict) -> list[dict]:
    base, key = _get_supabase_base()
    if not base or not key:
        return []
    qs = urllib.parse.urlencode(params, doseq=True)
    endpoint = f"{base}/{table}"
    if qs:
        endpoint = f"{endpoint}?{qs}"
    req = urllib.request.Request(endpoint, method="GET")
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else []
    except Exception as e:
        print(f"Supabase query error: {e}")
        return []

def _delete_rows(table: str, params: dict) -> bool:
    base, key = _get_supabase_base()
    if not base or not key:
        return False
    qs = urllib.parse.urlencode(params, doseq=True)
    endpoint = f"{base}/{table}"
    if qs:
        endpoint = f"{endpoint}?{qs}"
    req = urllib.request.Request(endpoint, method="DELETE")
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Prefer", "return=minimal")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"Supabase delete error: {e}")
        return False

def insert_members(members: list[dict]):
    rows = []
    for m in members:
        rows.append({
            "username": m.get("username")
        })
    if not rows:
        return
    chunk_size = 200
    for i in range(0, len(rows), chunk_size):
        _post_rows("members", rows[i:i + chunk_size])

def insert_chat_messages(messages: list[dict]):
    rows = []
    for m in messages:
        rows.append({
            "content": m.get("content")
        })
    if not rows:
        return
    chunk_size = 200
    for i in range(0, len(rows), chunk_size):
        _post_rows("chat_messages", rows[i:i + chunk_size])

def fetch_members(limit: int = 2000) -> list[str]:
    params = {"select": "username", "limit": str(limit)}
    rows = _get_rows("members", params)
    return [r.get("username") for r in rows if r.get("username")]

def fetch_chat_messages(limit: int = 500) -> list[str]:
    params = {"select": "content", "limit": str(limit)}
    rows = _get_rows("chat_messages", params)
    return [r.get("content") for r in rows if r.get("content")]

def upsert_proxy(proxy: dict) -> bool:
    if not proxy:
        return False
    row = {
        "id": proxy.get("id"),
        "scheme": proxy.get("scheme"),
        "host": proxy.get("host"),
        "port": proxy.get("port"),
        "username": proxy.get("username"),
        "password": proxy.get("password"),
        "raw_url": proxy.get("raw_url"),
        "enabled": proxy.get("enabled"),
        "last_check": proxy.get("last_check"),
        "ok": proxy.get("ok")
    }
    return _post_rows("proxies", [row], upsert=True)

def delete_proxy(proxy: dict) -> bool:
    if not proxy:
        return False
    if proxy.get("id"):
        return _delete_rows("proxies", {"id": f"eq.{proxy.get('id')}"})
    if proxy.get("raw_url"):
        return _delete_rows("proxies", {"raw_url": f"eq.{proxy.get('raw_url')}"})
    if proxy.get("host") and proxy.get("port"):
        return _delete_rows("proxies", {"host": f"eq.{proxy.get('host')}", "port": f"eq.{proxy.get('port')}"})
    return False

def save_account_session(phone: str) -> bool:
    session_path = f"account/{phone}.session"
    json_path = f"data/{phone}.json"
    try:
        with open(session_path, "rb") as f:
            session_b64 = base64.b64encode(f.read()).decode("utf-8")
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
    except Exception:
        return False
    row = {
        "phone": phone,
        "session_b64": session_b64,
        "json_data": json.dumps(json_data, ensure_ascii=False),
        "updated_at": int(time.time())
    }
    return _post_rows("account_sessions", [row], upsert=True)

def fetch_account_session(phone: str) -> dict:
    rows = _get_rows("account_sessions", {"select": "phone,session_b64,json_data", "phone": f"eq.{phone}", "limit": "1"})
    return rows[0] if rows else {}
