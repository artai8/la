from fastapi import APIRouter, Depends
from app.models import SettingsRequest, ApiCredentialRequest, ApiUpdateRequest, ApiToggleRequest, ApiImportRequest, ProxyRequest, ProxyUpdateRequest, ProxyToggleRequest, ProxyImportRequest
from app.core.database import (
    get_setting, set_setting, list_api_credentials, add_api_credential,
    update_api_credential, set_api_enabled, remove_api_credential, import_api_credentials,
    list_proxies, add_proxy, update_proxy, set_proxy_enabled, remove_proxy, import_proxies
)
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/settings")

@router.get("")
async def settings_list(user=Depends(get_current_user)):
    return {
        "db1_host": get_setting("db1_host"),
        "db1_port": get_setting("db1_port"),
        "db1_user": get_setting("db1_user"),
        "db1_pass": get_setting("db1_pass"),
        "db1_name": get_setting("db1_name"),
        "v2ray_path": get_setting("v2ray_path"),
        "max_concurrent": get_setting("max_concurrent", "5")
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
    add_proxy(req.scheme, req.host, req.port, req.username, req.password, req.raw_url)
    return {"status": True}

@router.post("/proxy/update")
async def proxy_update(req: ProxyUpdateRequest, user=Depends(get_current_user)):
    update_proxy(req.id, req.scheme, req.host, req.port, req.username, req.password, req.raw_url)
    return {"status": True}

@router.post("/proxy/toggle")
async def proxy_toggle(req: ProxyToggleRequest, user=Depends(get_current_user)):
    set_proxy_enabled(req.id, 1 if req.enabled else 0)
    return {"status": True}

@router.post("/proxy/remove")
async def proxy_remove(req: ProxyToggleRequest, user=Depends(get_current_user)):
    remove_proxy(req.id)
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
