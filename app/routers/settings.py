from fastapi import APIRouter, Depends
from app.models import SettingsRequest, ApiCredentialRequest, ApiUpdateRequest, ApiToggleRequest, ApiImportRequest, ProxyRequest, ProxyUpdateRequest, ProxyToggleRequest, ProxyImportRequest
from app.core.database import (
    get_setting, set_setting, list_api_credentials, add_api_credential,
    update_api_credential, set_api_enabled, remove_api_credential, import_api_credentials,
    list_proxies, add_proxy, update_proxy, set_proxy_enabled, remove_proxy, import_proxies,
    update_proxy_check, get_proxy
)
from app.core.db_remote import upsert_proxy, delete_proxy
from app.core.auth import get_current_user
from app.core.telegram import TelegramPanel
from app.core.v2ray import V2RayController

router = APIRouter(prefix="/api/settings")

@router.get("")
async def settings_list(user=Depends(get_current_user)):
    return {
        "settings": {
            "min_delay": get_setting("min_delay"),
            "max_delay": get_setting("max_delay"),
            "flood_wait_limit": get_setting("flood_wait_limit"),
            "max_errors": get_setting("max_errors"),
            "max_members_limit": get_setting("max_members_limit"),
            "max_concurrent": get_setting("max_concurrent", "5"),
            "chat_interval_min": get_setting("chat_interval_min"),
            "chat_interval_max": get_setting("chat_interval_max"),
            "chat_messages": get_setting("chat_messages"),
            "lang": get_setting("lang"),
            "db_url": get_setting("db_url") or get_setting("db1_url"),
            "db_key": get_setting("db_key") or get_setting("db1_key"),
            "v2ray_path": get_setting("v2ray_path")
        }
    }

@router.post("")
async def settings_update(req: SettingsRequest, user=Depends(get_current_user)):
    set_setting(req.key, req.value)
    return {"status": True}

@router.get("/api")
async def api_list(user=Depends(get_current_user)):
    return {"items": list_api_credentials()}

@router.post("/api")
async def api_add(req: ApiCredentialRequest, user=Depends(get_current_user)):
    add_api_credential(req.api_id, req.api_hash)
    return {"status": True}

@router.post("/api/update")
async def api_update(req: ApiUpdateRequest, user=Depends(get_current_user)):
    update_api_credential(req.id, req.api_id, req.api_hash)
    return {"status": True}

@router.post("/api/toggle")
async def api_toggle(req: ApiToggleRequest, user=Depends(get_current_user)):
    set_api_enabled(req.id, 1 if req.enabled else 0)
    return {"status": True}

@router.post("/api/remove")
async def api_remove(req: ApiToggleRequest, user=Depends(get_current_user)):
    remove_api_credential(req.id)
    return {"status": True}

@router.post("/api/import")
async def api_import(req: ApiImportRequest, user=Depends(get_current_user)):
    lines = [l.strip() for l in req.lines.splitlines() if l.strip()]
    items = []
    for line in lines:
        parts = line.split(":") if ":" in line else line.split("|")
        if len(parts) >= 2:
            try:
                items.append((int(parts[0]), parts[1]))
            except:
                pass
    import_api_credentials(items)
    return {"status": True, "count": len(items)}

@router.get("/proxy")
async def proxy_list(user=Depends(get_current_user)):
    return {"items": list_proxies()}

@router.post("/proxy")
async def proxy_add(req: ProxyRequest, user=Depends(get_current_user)):
    if req.raw_url:
        scheme = "socks5"
        host = "127.0.0.1"
        port = 1080
        username = ""
        password = ""
    else:
        scheme = req.scheme or "socks5"
        host = req.host
        port = req.port
        username = req.username
        password = req.password
        if not host or not port:
            return {"status": False, "message": "代理参数不完整"}
    row_id = add_proxy(scheme, host, port, username, password, req.raw_url)
    ok = None
    if row_id:
        if req.raw_url:
            port, err = V2RayController.start(req.raw_url)
            if err:
                ok = False
            else:
                ok = await TelegramPanel.check_proxy("127.0.0.1", port, "", "")
                V2RayController.stop(port)
        else:
            ok = await TelegramPanel.check_proxy(host, port, username, password)
        if ok is not None:
            update_proxy_check(row_id, 1 if ok else 0)
        proxy = get_proxy(row_id)
        if ok:
            upsert_proxy(proxy)
        else:
            delete_proxy(proxy)
    return {"status": True, "added": True, "valid": ok}

@router.post("/proxy/update")
async def proxy_update(req: ProxyUpdateRequest, user=Depends(get_current_user)):
    if req.raw_url:
        scheme = "socks5"
        host = "127.0.0.1"
        port = 1080
        username = ""
        password = ""
    else:
        scheme = req.scheme or "socks5"
        host = req.host
        port = req.port
        username = req.username
        password = req.password
        if not host or not port:
            return {"status": False, "message": "代理参数不完整"}
    update_proxy(req.id, scheme, host, port, username, password, req.raw_url)
    proxy = get_proxy(req.id)
    if proxy:
        upsert_proxy(proxy)
    return {"status": True}

@router.post("/proxy/toggle")
async def proxy_toggle(req: ProxyToggleRequest, user=Depends(get_current_user)):
    set_proxy_enabled(req.id, 1 if req.enabled else 0)
    proxy = get_proxy(req.id)
    if req.enabled:
        upsert_proxy(proxy)
    else:
        delete_proxy(proxy)
    return {"status": True}

@router.post("/proxy/remove")
async def proxy_remove(req: ProxyToggleRequest, user=Depends(get_current_user)):
    remove_proxy(req.id)
    delete_proxy({"id": req.id})
    return {"status": True}

@router.post("/proxy/import")
async def proxy_import(req: ProxyImportRequest, user=Depends(get_current_user)):
    lines = [l.strip() for l in req.lines.splitlines() if l.strip()]
    items = []
    for line in lines:
        # Simple parse: scheme:host:port:user:pass or host:port:user:pass or raw_url
        if "://" in line and not line.startswith("socks5://"):
            # vless/vmess
            items.append(("socks5", "127.0.0.1", 1080, "", "", line))
            continue
            
        parts = line.replace("://", ":").split(":")
        # default scheme socks5
        scheme = "socks5"
        host = parts[0]
        port = 1080
        user = ""
        pwd = ""
        
        try:
            if len(parts) >= 2:
                port = int(parts[1])
            if len(parts) >= 4:
                user = parts[2]
                pwd = parts[3]
            items.append((scheme, host, port, user, pwd, ""))
        except:
            pass
            
    import_proxies(items)
    return {"status": True, "count": len(items)}

@router.post("/proxy/test")
async def proxy_test(req: ProxyToggleRequest, user=Depends(get_current_user)):
    proxy = get_proxy(req.id)
    if not proxy:
        return {"status": False, "ok": False}
    if proxy.get("raw_url"):
        port, err = V2RayController.start(proxy["raw_url"])
        if err:
            update_proxy_check(req.id, 0)
            return {"status": True, "ok": False}
        ok = await TelegramPanel.check_proxy("127.0.0.1", port, "", "")
        V2RayController.stop(port)
    else:
        ok = await TelegramPanel.check_proxy(proxy["host"], proxy["port"], proxy.get("username"), proxy.get("password"))
    update_proxy_check(req.id, 1 if ok else 0)
    proxy = get_proxy(req.id)
    if ok:
        upsert_proxy(proxy)
    else:
        delete_proxy(proxy)
    return {"status": True, "ok": ok}
