import asyncio
from fastapi import APIRouter, Depends
from pyrogram import Client
from app.models import PhoneRequest, CodeRequest, PasswordRequest, ProfileEditRequest, GroupAssignRequest
from app.core.telegram import TelegramPanel
from app.core.auth import get_current_user
from app.database import _db

router = APIRouter(prefix="/api")
login_sessions: dict[str, dict] = {}

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
    phone = req.phone.strip()
    if len(phone) < 4:
        return {"status": False, "message": "Phone number too short"}
    if not phone.startswith("+") or not phone[1:].isdigit():
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
    return {"status": True, "message": "Code sent", "needs": "code"}

@router.post("/account/verify-code")
async def verify_code(req: CodeRequest, user=Depends(get_current_user)):
    phone = req.phone.strip()
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
    phone = req.phone.strip()
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
    phone = req.phone.strip()
    if phone in login_sessions:
        await TelegramPanel.cancel_account(login_sessions[phone]["cli"], phone)
        del login_sessions[phone]
    return {"status": True}

@router.post("/account/remove")
async def remove_account(req: PhoneRequest, user=Depends(get_current_user)):
    phone = req.phone.strip()
    if phone in TelegramPanel.list_accounts():
        TelegramPanel.remove_account(phone)
        return {"status": True, "message": f"{phone} removed"}
    return {"status": False, "message": "Not found"}
