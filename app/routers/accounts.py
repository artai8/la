import asyncio
import os
import re
import time
from fastapi import APIRouter, Depends
from pyrogram import Client
from app.models import PhoneRequest, CodeRequest, PasswordRequest, ProfileEditRequest, GroupAssignRequest, SessionImportRequest, SessionBatchImportRequest, AccountKeepaliveRequest, AccountWarmupRequest, AccountSpamCheckRequest
from app.core.telegram import TelegramPanel
from app.core.auth import get_current_user
from app.core.database import _db
from app.core.tasks import warmup_process, start_keepalive, stop_keepalive, _connect_clients
from app.state import state

router = APIRouter(prefix="/api")
login_sessions: dict[str, dict] = {}

def _normalize_phone(raw: str) -> str:
    phone = re.sub(r"[^\d+]", "", raw or "")
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    if phone.startswith("+"):
        return phone if phone[1:].isdigit() else ""
    return "+" + phone if phone.isdigit() else ""

def _session_path(name: str) -> str:
    return f"{name}.session"

async def _import_session(api_id: int, api_hash: str, session_string: str) -> dict:
    if not session_string:
        return {"ok": False, "message": "Session 不能为空"}
    os.makedirs("account", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    temp_name = f"account/import_{int(time.time() * 1000)}"
    temp_path = _session_path(temp_name)
    cli = Client(temp_name, api_id, api_hash, session_string=session_string)
    try:
        await asyncio.wait_for(cli.connect(), 15)
        me = await cli.get_me()
        if not me or not getattr(me, "phone_number", None):
            return {"ok": False, "message": "无法获取手机号"}
        phone = me.phone_number
        if not phone.startswith("+"):
            phone = "+" + phone
        if phone in TelegramPanel.list_accounts():
            return {"ok": False, "message": f"{phone} 已存在"}
        await cli.disconnect()
        final_path = _session_path(f"account/{phone}")
        if os.path.exists(final_path):
            return {"ok": False, "message": f"{phone} 已存在"}
        if os.path.exists(temp_path):
            os.replace(temp_path, final_path)
        TelegramPanel.make_json_data(phone, api_id, api_hash, "", "")
        return {"ok": True, "phone": phone}
    except Exception as e:
        try:
            await TelegramPanel._safe_disconnect(cli, None)
        except Exception:
            pass
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return {"ok": False, "message": str(e)}

@router.get("/accounts")
async def get_accounts(user=Depends(get_current_user)):
    accs = TelegramPanel.list_accounts()
    # Fetch group info from DB
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("select phone, group_name from accounts")
        rows = {r["phone"]: r["group_name"] for r in cur.fetchall()}
    except Exception:
        rows = {}
    conn.close()
    
    result = []
    for p in accs:
        result.append({
            "phone": p,
            "group": rows.get(p, "default")
        })
    return {"accounts": result, "count": len(accs)}

@router.post("/accounts/group/set")
async def set_account_group(req: GroupAssignRequest, user=Depends(get_current_user)):
    conn = _db()
    cur = conn.cursor()
    for phone in req.phones:
        try:
            cur.execute("insert or ignore into accounts(phone, status, last_check, note, group_name, profile_updated_at) values(?, '', 0, '', ?, 0)", (phone, req.group_name))
            cur.execute("update accounts set group_name=? where phone=?", (req.group_name, phone))
        except Exception:
            pass
    conn.commit()
    conn.close()
    return {"status": True}

@router.post("/accounts/profile/update")
async def update_profile_batch(req: ProfileEditRequest, user=Depends(get_current_user)):
    if not req.phones:
        return {"status": False, "message": "No accounts selected"}
    
    sem = asyncio.Semaphore(5)
    results = []
    
    async def worker(phone):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return {"phone": phone, "ok": False, "error": "No data"}
            
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await asyncio.wait_for(cli.connect(), 15)
                res = await TelegramPanel.update_profile(cli, req.first_name, req.last_name, req.about, req.username)
                return {"phone": phone, "ok": res["ok"], "error": res.get("error")}
            except Exception as e:
                return {"phone": phone, "ok": False, "error": str(e)}
            finally:
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(worker(p)) for p in req.phones]
    if tasks:
        results = await asyncio.gather(*tasks)
        
    return {"status": True, "results": results}

@router.get("/accounts/health")
async def accounts_health(user=Depends(get_current_user)):
    accs = TelegramPanel.list_accounts()
    results = []
    sem = asyncio.Semaphore(5)

    async def check_one(phone):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return {"phone": phone, "ok": False, "message": "missing data"}
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await asyncio.wait_for(cli.connect(), 12)
                me = await cli.get_me()
                return {"phone": phone, "ok": True, "message": me.first_name or ""}
            except Exception as e:
                return {"phone": phone, "ok": False, "message": str(e)}
            finally:
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(check_one(p)) for p in accs]
    if tasks:
        results = await asyncio.gather(*tasks)
    return {"items": results}

@router.post("/account/send-code")
async def send_code(req: PhoneRequest, user=Depends(get_current_user)):
    phone = _normalize_phone(req.phone.strip())
    if len(phone) < 5:
        return {"status": False, "message": "Phone number too short"}
    if not phone.startswith("+"):
        return {"status": False, "message": "Invalid phone format"}

    result = await TelegramPanel.add_account(phone)
    if not result["status"]:
        return {"status": False, "message": result["message"]}

    login_sessions[phone] = {
        "cli": result["cli"],
        "code_hash": result["code_hash"],
        "api_id": result["api_id"],
        "api_hash": result["api_hash"],
        "proxy": result["proxy"],
    }
    return {"status": True, "message": "Code sent", "needs": "code", "phone": phone}

@router.post("/account/verify-code")
async def verify_code(req: CodeRequest, user=Depends(get_current_user)):
    phone = _normalize_phone(req.phone.strip())
    if phone not in login_sessions:
        return {"status": False, "message": "No pending login"}

    s = login_sessions[phone]
    r = await TelegramPanel.verify_code(s["cli"], phone, s["code_hash"], req.code)

    if r["status"]:
        TelegramPanel.make_json_data(phone, s["api_id"], s["api_hash"], s["proxy"], "")
        del login_sessions[phone]
        return {"status": True, "message": r["message"]}
    if r["message"] == "FA2":
        return {"status": False, "message": "FA2", "needs": "password"}
    if r["message"] == "invalid_code":
        return {"status": False, "message": "Invalid code"}
    del login_sessions[phone]
    return {"status": False, "message": r["message"]}

@router.post("/account/verify-password")
async def verify_password(req: PasswordRequest, user=Depends(get_current_user)):
    phone = _normalize_phone(req.phone.strip())
    if phone not in login_sessions:
        return {"status": False, "message": "No pending login"}

    s = login_sessions[phone]
    r = await TelegramPanel.verify_password(s["cli"], phone, req.password)

    if r["status"]:
        TelegramPanel.make_json_data(phone, s["api_id"], s["api_hash"], s["proxy"], req.password)
        del login_sessions[phone]
        return {"status": True, "message": r["message"]}
    if r["message"] == "invalid_password":
        return {"status": False, "message": "Invalid password"}
    del login_sessions[phone]
    return {"status": False, "message": r["message"]}

@router.post("/account/cancel")
async def cancel_login(req: PhoneRequest, user=Depends(get_current_user)):
    phone = _normalize_phone(req.phone.strip())
    if phone in login_sessions:
        await TelegramPanel.cancel_account(login_sessions[phone]["cli"], phone)
        del login_sessions[phone]
    return {"status": True}

@router.post("/account/remove")
async def remove_account(req: PhoneRequest, user=Depends(get_current_user)):
    phone = _normalize_phone(req.phone.strip())
    if phone in TelegramPanel.list_accounts():
        TelegramPanel.remove_account(phone)
        return {"status": True, "message": f"{phone} removed"}
    return {"status": False, "message": "Not found"}

@router.post("/account/import/session")
async def import_session(req: SessionImportRequest, user=Depends(get_current_user)):
    res = await _import_session(req.api_id, req.api_hash, req.session_string.strip())
    if res.get("ok"):
        return {"status": True, "phone": res.get("phone")}
    return {"status": False, "message": res.get("message", "Import failed")}

@router.post("/account/import/session/batch")
async def import_session_batch(req: SessionBatchImportRequest, user=Depends(get_current_user)):
    lines = [l.strip() for l in (req.lines or "").splitlines() if l.strip()]
    if not lines:
        return {"status": False, "message": "No lines"}
    results = []
    for line in lines:
        sep = "|" if "|" in line else "," if "," in line else ":"
        parts = [p.strip() for p in line.split(sep)]
        if len(parts) < 3:
            results.append({"line": line, "ok": False, "message": "格式错误"})
            continue
        api_id_raw, api_hash = parts[0], parts[1]
        session_string = sep.join(parts[2:]).strip()
        try:
            api_id = int(api_id_raw)
        except Exception:
            results.append({"line": line, "ok": False, "message": "API ID 无效"})
            continue
        res = await _import_session(api_id, api_hash, session_string)
        results.append({"line": line, "ok": res.get("ok"), "phone": res.get("phone"), "message": res.get("message")})
    return {"status": True, "results": results}

@router.post("/accounts/keepalive/start")
async def accounts_keepalive_start(req: AccountKeepaliveRequest, user=Depends(get_current_user)):
    phones = [p for p in req.phones if p in TelegramPanel.list_accounts()]
    if not phones:
        return {"status": False, "message": "No accounts selected"}
    if state.status or state.extract or state.chat_active:
        return {"status": False, "message": "Busy"}
    clients = await _connect_clients(phones)
    if not clients:
        return {"status": False, "message": "No clients connected"}
    await start_keepalive(clients)
    return {"status": True, "count": len(clients)}

@router.post("/accounts/keepalive/stop")
async def accounts_keepalive_stop(user=Depends(get_current_user)):
    await stop_keepalive()
    return {"status": True}

@router.post("/accounts/warmup/start")
async def accounts_warmup_start(req: AccountWarmupRequest, user=Depends(get_current_user)):
    phones = [p for p in req.phones if p in TelegramPanel.list_accounts()]
    if not phones:
        return {"status": False, "message": "No accounts selected"}
    if state.status or state.extract or state.chat_active:
        return {"status": False, "message": "Busy"}
    actions = req.actions or ["scroll", "read"]
    asyncio.create_task(warmup_process(phones, int(req.duration_min), actions))
    return {"status": True, "count": len(phones)}

@router.post("/accounts/spam/check")
async def accounts_spam_check(req: AccountSpamCheckRequest, user=Depends(get_current_user)):
    phones = [p for p in req.phones if p in TelegramPanel.list_accounts()]
    if not phones:
        return {"status": False, "message": "No accounts selected"}
    sem = asyncio.Semaphore(5)
    results = []

    async def check_one(phone: str):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return {"phone": phone, "ok": False, "message": "No data"}
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await asyncio.wait_for(cli.connect(), 12)
                await cli.send_message("SpamBot", "/start")
                await asyncio.sleep(1)
                msg = None
                async for m in cli.get_chat_history("SpamBot", limit=1):
                    msg = m
                text = (msg.text or msg.caption or "") if msg else ""
                lower = text.lower()
                limited = False
                if "limited" in lower or "limit" in lower or "限制" in text or "spam" in lower:
                    limited = True
                if "no limits" in lower or "not limited" in lower or "没有限制" in text or "未发现限制" in text:
                    limited = False
                return {"phone": phone, "ok": True, "limited": limited, "message": text}
            except Exception as e:
                return {"phone": phone, "ok": False, "message": str(e)}
            finally:
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(check_one(p)) for p in phones]
    if tasks:
        results = await asyncio.gather(*tasks)
    return {"status": True, "items": results}
