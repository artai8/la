import asyncio
from fastapi import APIRouter, Depends
from app.models import ExtractRequest, ExtractBatchRequest, NameRequest, _normalize_links, _normalize_keywords
from app.core.telegram import TelegramPanel
from app.core.auth import get_current_user
from app.core.tasks import extract_process, extract_batch_process
from app.state import state
from app.core.ws import manager

router = APIRouter(prefix="/api")

@router.get("/groups")
async def get_groups(user=Depends(get_current_user)):
    return {"groups": TelegramPanel.list_groups()}

@router.post("/extract/start")
async def start_extract(req: ExtractRequest, user=Depends(get_current_user)):
    if state.extract:
        return {"status": False, "message": "Already extracting"}
    if not TelegramPanel.list_accounts():
        return {"status": False, "message": "No accounts"}
    if not TelegramPanel.is_valid_telegram_link(req.link.strip()):
        return {"status": False, "message": "Invalid link"}

    state.extract = True
    state.members_ext = []
    include_keywords = _normalize_keywords(req.include_keywords)
    exclude_keywords = _normalize_keywords(req.exclude_keywords)
    asyncio.create_task(extract_process(req.link.strip(), include_keywords, exclude_keywords, req.auto_load, None, True))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/extract/batch")
async def start_extract_batch(req: ExtractBatchRequest, user=Depends(get_current_user)):
    if state.extract:
        return {"status": False, "message": "Already extracting"}
    accs = TelegramPanel.list_accounts()
    if not accs:
        return {"status": False, "message": "No accounts"}
    links = _normalize_links(req.links)
    links = [l for l in links if TelegramPanel.is_valid_telegram_link(l)]
    if not links:
        return {"status": False, "message": "No valid links"}
    state.extract = True
    state.members_ext = []
    include_keywords = _normalize_keywords(req.include_keywords)
    exclude_keywords = _normalize_keywords(req.exclude_keywords)
    asyncio.create_task(extract_batch_process(links, include_keywords, exclude_keywords, req.auto_load))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/extract/stop")
async def stop_extract(user=Depends(get_current_user)):
    if not state.extract:
        return {"status": False, "message": "Not active"}
    state.extract = False
    await manager.send_state()
    return {"status": True, "message": "Stopped"}

@router.post("/group/remove")
async def remove_group(req: NameRequest, user=Depends(get_current_user)):
    if TelegramPanel.remove_group(req.name):
        return {"status": True, "message": f"'{req.name}' deleted"}
    return {"status": False, "message": "Not found"}
