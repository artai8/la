from fastapi import APIRouter, Depends
from app.models import AdderRequest, JoinRequest, InviteRequest, ChatRequest, DMRequest
from app.core.tasks import create_task
from app.core.database import _now_ts
from app.state import state
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/adder")

@router.post("/join")
async def adder_join(req: JoinRequest, user=Depends(get_current_user)):
    task_id = create_task("join", req.dict(), _now_ts())
    return {"status": True, "task_id": task_id}

@router.post("/invite")
async def adder_invite(req: InviteRequest, user=Depends(get_current_user)):
    task_id = create_task("adder", req.dict(), _now_ts())
    return {"status": True, "task_id": task_id}

@router.post("/chat")
async def adder_chat(req: ChatRequest, user=Depends(get_current_user)):
    task_id = create_task("chat", req.dict(), _now_ts())
    return {"status": True, "task_id": task_id}

@router.post("/dm")
async def adder_dm(req: DMRequest, user=Depends(get_current_user)):
    task_id = create_task("dm", req.dict(), _now_ts())
    return {"status": True, "task_id": task_id}

@router.post("/stop")
async def adder_stop(user=Depends(get_current_user)):
    state.status = False
    return {"status": True}
