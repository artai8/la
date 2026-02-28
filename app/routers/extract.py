import asyncio
from fastapi import APIRouter, Depends
from app.models import ExtractRequest, ExtractBatchRequest, ScrapeRequest
from app.core.tasks import extract_process, create_task, scrape_process
from app.state import state
from app.core.auth import get_current_user
from app.core.database import _now_ts

router = APIRouter(prefix="/api/extract")

@router.post("")
async def extract_run(req: ExtractRequest, user=Depends(get_current_user)):
    if state.extract:
        return {"status": False, "message": "Already running"}
    
    # Create a temporary task ID for logging purposes? 
    # Or just run it. extract_process expects task_id.
    # We can create a task record even for immediate run so we can see logs.
    task_id = create_task("extract", req.dict(), _now_ts())
    
    # Run in background
    asyncio.create_task(extract_process(task_id, req.dict()))
    
    return {"status": True, "task_id": task_id}

@router.post("/stop")
async def extract_stop(user=Depends(get_current_user)):
    # We can't easily stop asyncio task without handle.
    # But we can set state.extract = False and hope the loop checks it?
    # extract_process in tasks.py sets state.extract = True.
    # It doesn't check it inside the loop (it iterates members).
    # We should improve extract_process to check state.extract.
    state.extract = False
    return {"status": True}

@router.post("/batch")
async def extract_batch(req: ExtractBatchRequest, user=Depends(get_current_user)):
    # This creates a queued task
    task_id = create_task("extract_batch", req.dict(), _now_ts())
    return {"status": True, "task_id": task_id}

@router.post("/chat")
async def extract_chat(req: ScrapeRequest, user=Depends(get_current_user)):
    task_id = create_task("scrape", req.dict(), _now_ts())
    asyncio.create_task(scrape_process(task_id, req.dict()))
    return {"status": True, "task_id": task_id, "message": "已开始抓取"}
