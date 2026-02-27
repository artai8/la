import asyncio
from fastapi import APIRouter, Depends
from app.models import SettingsRequest, ApiCredentialRequest, ApiUpdateRequest, ApiToggleRequest, ApiImportRequest, ProxyRequest, ProxyUpdateRequest, ProxyToggleRequest, ProxyImportRequest, TaskIdRequest
from app.core.auth import get_current_user, require_admin
from app.core.telegram import TelegramPanel
from app.core.v2ray import V2RayController
from app.state import state
from app.database import (
    get_setting, set_setting, list_api_credentials, add_api_credential, update_api_credential,
    set_api_enabled, import_api_credentials, remove_api_credential, list_proxies, add_proxy,
    update_proxy, set_proxy_enabled, import_proxies, remove_proxy, update_proxy_check
)

router = APIRouter(prefix="/api")

@router.get("/state")
async def get_state_route(user=Depends(get_current_user)):
    return state.to_dict()

@router.get("/settings")
async def get_settings(user=Depends(require_admin)):
    keys = [
        "min_delay", "max_delay", "flood_wait_limit", "max_errors", "max_members_limit", "max_concurrent",
        "lang", "chat_interval_min", "chat_interval_max", "chat_messages",
        "db1_host", "db1_port", "db1_user", "db1_pass", "db1_name",
        "db2_host", "db2_port", "db2_user", "db2_pass", "db2_name",
        "v2ray_use_docker",
    ]
    settings = {k: get_setting(k) for k in keys}
    if not settings.get("max_concurrent") or settings.get("max_concurrent") == "0":
         settings["max_concurrent"] = str(state.max_concurrent)
    return {"settings": settings}

@router.post("/settings/set")
async def set_settings(req: SettingsRequest, user=Depends(require_admin)):
    if req.key == "max_concurrent":
        try:
            val = int(req.value)
            if val < 0: val = 0
            if val > 0:
                state.max_concurrent = val
            else:
                state.max_concurrent = TelegramPanel.get_max_concurrent()
            set_setting(req.key, str(val))
        except Exception:
            pass
    else:
        set_setting(req.key, str(req.value))
    return {"status": True}

@router.get("/apis")
async def get_apis(user=Depends(require_admin)):
    return {"items": list_api_credentials()}

@router.post("/apis/add")
async def add_apis(req: ApiCredentialRequest, user=Depends(require_admin)):
    add_api_credential(req.api_id, req.api_hash)
    return {"status": True}

@router.post("/apis/update")
async def update_apis(req: ApiUpdateRequest, user=Depends(require_admin)):
    update_api_credential(req.id, req.api_id, req.api_hash)
    return {"status": True}

@router.post("/apis/toggle")
async def toggle_apis(req: ApiToggleRequest, user=Depends(require_admin)):
    set_api_enabled(req.id, 1 if req.enabled else 0)
    return {"status": True}

@router.post("/apis/import")
async def import_apis(req: ApiImportRequest, user=Depends(require_admin)):
    lines = req.lines.splitlines()
    items = []
    for line in lines:
        parts = line.strip().split(":", 1)
        if len(parts) == 2 and parts[0].isdigit():
            items.append((int(parts[0]), parts[1].strip()))
    if items:
        import_api_credentials(items)
    return {"status": True, "count": len(items)}

@router.post("/apis/remove")
async def remove_apis(req: ApiToggleRequest, user=Depends(require_admin)):
    remove_api_credential(req.id)
    return {"status": True}

@router.get("/proxies")
async def get_proxies(user=Depends(require_admin)):
    return {"items": list_proxies()}

@router.post("/proxy/add")
async def add_proxy_item(req: ProxyRequest, user=Depends(get_current_user)):
    raw_url = req.raw_url.strip()
    if not raw_url:
        return {"status": False, "message": "Only v2ray links are supported"}
    
    # Check if already exists
    exists = [p for p in list_proxies() if p["raw_url"] == raw_url]
    if exists:
        return {"status": False, "message": "Proxy already exists"}

    add_proxy("", "", 0, "", "", raw_url)
    return {"status": True}

@router.post("/proxy/update")
async def update_proxy_item(req: ProxyUpdateRequest, user=Depends(get_current_user)):
    raw_url = req.raw_url.strip()
    if not raw_url:
        return {"status": False, "message": "Only v2ray links are supported"}

    update_proxy(req.id, "", "", 0, "", "", raw_url)
    return {"status": True}

@router.post("/proxies/toggle")
async def toggle_proxies(req: ProxyToggleRequest, user=Depends(require_admin)):
    set_proxy_enabled(req.id, 1 if req.enabled else 0)
    return {"status": True}

@router.post("/proxies/import")
async def import_proxies_route(req: ProxyImportRequest, user=Depends(require_admin)):
    lines = req.lines.splitlines()
    items = []
    for line in lines:
        # scheme://user:pass@host:port or host:port:user:pass
        line = line.strip()
        if not line: continue
        
        # Check for v2ray links
        if line.startswith("vmess://") or line.startswith("vless://") or line.startswith("trojan://"):
            items.append(("v2ray", "v2ray", 0, "", "", line))
            continue

        scheme = "socks5"
        username = ""
        password = ""
        host = ""
        port = 1080
        raw_url = ""
        
        if "://" in line:
            scheme, rest = line.split("://", 1)
            if "@" in rest:
                auth, addr = rest.split("@", 1)
                if ":" in auth:
                    username, password = auth.split(":", 1)
                else:
                    username = auth
                if ":" in addr:
                    host, port = addr.split(":", 1)
                else:
                    host = addr
            else:
                if ":" in rest:
                    host, port = rest.split(":", 1)
                else:
                    host = rest
        elif line.count(":") >= 3:
            # host:port:user:pass
            parts = line.split(":")
            host = parts[0]
            port = parts[1]
            username = parts[2]
            password = parts[3]
        elif line.count(":") == 1:
            host, port = line.split(":")
        
        try:
            port = int(port)
            items.append((scheme, host, port, username, password, raw_url))
        except:
            pass

    if items:
        import_proxies(items)
    return {"status": True, "count": len(items)}

@router.post("/proxies/remove")
async def remove_proxies(req: ProxyToggleRequest, user=Depends(require_admin)):
    remove_proxy(req.id)
    return {"status": True}

@router.post("/proxies/check")
async def check_proxies_route(req: ProxyToggleRequest, user=Depends(require_admin)):
    proxies = list_proxies()
    p = next((x for x in proxies if x["id"] == req.id), None)
    if not p:
        return {"status": False, "message": "Proxy not found"}
    
    ok = False
    if p.get("raw_url"):
        # V2Ray check
        use_docker = get_setting("v2ray_use_docker") == "1"
        loop = asyncio.get_running_loop()
        port, err = await loop.run_in_executor(None, V2RayController.start, p["raw_url"], use_docker)
        if not err and port:
            # Check via local port
            ok = await TelegramPanel.check_proxy("127.0.0.1", port, "", "")
            # Cleanup
            V2RayController.stop(port)
        else:
            print(f"Check v2ray failed: {err}")
    else:
        # Standard check
        ok = await TelegramPanel.check_proxy(p["host"], p["port"], p["username"], p["password"])
        
    update_proxy_check(req.id, 1 if ok else 0)
    return {"status": True, "ok": ok}
