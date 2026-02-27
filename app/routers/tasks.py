from datetime import datetime
from fastapi import APIRouter, Depends
from app.models import TaskCreateRequest, TaskIdRequest
from app.core.auth import get_current_user
from app.database import list_tasks, create_task, update_task_status, get_task_log, delete_task
from app.state import state
from app.core.ws import manager
from app.core.tasks import stop_keepalive

router = APIRouter(prefix="/api/tasks")

@router.get("")
async def get_tasks(user=Depends(get_current_user)):
    return {"items": list_tasks()}

@router.post("/create")
async def create_tasks(req: TaskCreateRequest, user=Depends(get_current_user)):
    run_at = req.run_at or int(datetime.utcnow().timestamp())
    task_id = create_task(req.type, req.payload, run_at)
    return {"status": True, "id": task_id}

@router.post("/stop")
async def stop_task(req: TaskIdRequest, user=Depends(get_current_user)):
    update_task_status(req.id, "stopped", finished_at=int(datetime.utcnow().timestamp()))
    if state.current_task_id == req.id:
        state.status = False
        state.extract = False
        state.chat_active = False
        state.current_task_id = None
        state.current_task_type = None
        await manager.send_state()
    if state.keepalive:
        await stop_keepalive()
    return {"status": True}

@router.post("/log")
async def task_log(req: TaskIdRequest, user=Depends(get_current_user)):
    return {"log": get_task_log(req.id)}

@router.post("/delete")
async def task_delete(req: TaskIdRequest, user=Depends(get_current_user)):
    delete_task(req.id)
    return {"status": True}
