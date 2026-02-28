from fastapi import APIRouter, Depends
from app.models import TaskCreateRequest, TaskIdRequest, TaskLogRequest
from app.core.database import list_tasks, create_task, delete_task, get_task_log, update_task_status
from app.core.auth import get_current_user
from app.core.database import _now_ts

router = APIRouter(prefix="/api/tasks")

@router.get("")
async def tasks_list(user=Depends(get_current_user)):
    return {"tasks": list_tasks()}

@router.post("/create")
async def tasks_create(req: TaskCreateRequest, user=Depends(get_current_user)):
    run_at = req.run_at or _now_ts()
    task_id = create_task(req.type, req.payload, run_at)
    return {"status": True, "task_id": task_id}

@router.post("/log")
async def tasks_log(req: TaskLogRequest, user=Depends(get_current_user)):
    return {"log": get_task_log(req.id)}

@router.post("/stop")
async def tasks_stop(req: TaskIdRequest, user=Depends(get_current_user)):
    # Simple stop: just mark as failed or done?
    # Actual stopping of running task requires cancellation token which we don't track well yet.
    # But we can update status so loop doesn't pick it up if queued.
    update_task_status(req.id, "stopped", finished_at=_now_ts())
    return {"status": True}

@router.post("/delete")
async def tasks_delete(req: TaskIdRequest, user=Depends(get_current_user)):
    delete_task(req.id)
    return {"status": True}
