import asyncio
from fastapi import APIRouter, Depends
from app.models import NameRequest, AdderRequest, InviteRequest, JoinRequest, ChatRequest, DMRequest, WarmupRequest, _normalize_links
from app.core.telegram import TelegramPanel
from app.core.auth import get_current_user
from app.core.tasks import load_members_from_group, load_members_from_groups, run_adder, join_groups_process, chat_process, dm_process, warmup_process
from app.state import state
from app.core.ws import manager

router = APIRouter(prefix="/api")

@router.post("/dm/start")
async def start_dm(req: DMRequest, user=Depends(get_current_user)):
    if state.status:
        return {"status": False, "message": "Task active"}
    accs = TelegramPanel.list_accounts()
    if not accs:
        return {"status": False, "message": "No accounts"}
    if not req.messages:
        return {"status": False, "message": "No messages"}
    
    number_account = min(req.number_account or 1, len(accs))
    asyncio.create_task(dm_process(req.group_name, accs[:number_account], req.messages, req.min_delay, req.max_delay))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/warmup/start")
async def start_warmup(req: WarmupRequest, user=Depends(get_current_user)):
    if state.status:
        return {"status": False, "message": "Task active"}
    accs = TelegramPanel.list_accounts()
    if not accs:
        return {"status": False, "message": "No accounts"}
    
    number_account = min(req.number_account or 1, len(accs))
    asyncio.create_task(warmup_process(accs[:number_account], req.duration_min, req.actions))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/members/load")
async def load_members(req: NameRequest, user=Depends(get_current_user)):
    if state.status:
        return {"status": False, "message": "Adder active"}
    added, skipped = await load_members_from_group(req.name)
    await manager.send_state()
    return {"status": True, "message": f"Loaded {added} (skipped: {skipped}, total: {len(state.members)})"}

@router.post("/members/clear")
async def clear_members(user=Depends(get_current_user)):
    if state.status:
        return {"status": False, "message": "Adder active"}
    state.members = []
    await manager.send_state()
    return {"status": True, "message": "Cleared"}

@router.post("/adder/start")
async def start_adder(req: AdderRequest, user=Depends(get_current_user)):
    if state.status:
        return {"status": False, "message": "Already active"}
    if not state.members:
        return {"status": False, "message": "No members loaded"}

    link = req.link.strip()
    if not (link.startswith("-100") and link.replace("-100", "").isdigit()):
        return {"status": False, "message": "Invalid ChatID"}

    accs = TelegramPanel.list_accounts()
    if req.number_account > len(accs) or req.number_account < 1:
        return {"status": False, "message": "Not enough accounts"}
    if req.number_add < 1:
        return {"status": False, "message": "Invalid add count"}

    state.status = True
    state.reset_adder()
    asyncio.create_task(run_adder(link, req.number_add, req.number_account))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/invite/start")
async def start_invite(req: InviteRequest, user=Depends(get_current_user)):
    if state.status:
        return {"status": False, "message": "Already active"}
    if req.group_names:
        state.members = []
        await load_members_from_groups(req.group_names)
    if req.use_loaded is False:
        state.members = []
    if not state.members:
        return {"status": False, "message": "No members loaded"}

    link = req.link.strip()
    accs = TelegramPanel.list_accounts()
    if req.number_account > len(accs) or req.number_account < 1:
        return {"status": False, "message": "Not enough accounts"}
    if req.number_add < 1:
        return {"status": False, "message": "Invalid add count"}

    state.status = True
    state.reset_adder()
    asyncio.create_task(run_adder(link, req.number_add, req.number_account))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/adder/stop")
async def stop_adder(user=Depends(get_current_user)):
    if not state.status:
        return {"status": False, "message": "Not active"}
    state.status = False
    await manager.send_state()
    return {"status": True, "message": "Stopped"}

@router.post("/join/start")
async def start_join(req: JoinRequest, user=Depends(get_current_user)):
    links = _normalize_links(req.links)
    links = [l for l in links if TelegramPanel.is_valid_telegram_link(l)]
    if not links:
        return {"status": False, "message": "No valid links"}
    accs = TelegramPanel.list_accounts()
    if not accs:
        return {"status": False, "message": "No accounts"}
    number_account = req.number_account or len(accs)
    number_account = min(number_account, len(accs))
    asyncio.create_task(join_groups_process(links, accs[:number_account]))
    return {"status": True, "message": "Started"}

@router.post("/chat/start")
async def start_chat(req: ChatRequest, user=Depends(get_current_user)):
    if state.chat_active:
        return {"status": False, "message": "Chat already running"}
    accs = TelegramPanel.list_accounts()
    if not accs:
        return {"status": False, "message": "No accounts"}
    if not req.messages:
        return {"status": False, "message": "No messages"}
    number_account = min(req.number_account or 1, len(accs))
    state.chat_active = True
    asyncio.create_task(chat_process(req.link.strip(), accs[:number_account], req.messages, req.min_delay, req.max_delay, req.max_messages))
    await manager.send_state()
    return {"status": True, "message": "Started"}

@router.post("/chat/stop")
async def stop_chat(user=Depends(get_current_user)):
    if not state.chat_active:
        return {"status": False, "message": "Not active"}
    state.chat_active = False
    await manager.send_state()
    return {"status": True, "message": "Stopped"}
