"""日志路由"""
import asyncio
import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.database import get_db, async_session_factory
from app.models import TaskLog, Task, Account
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    module: str = "",
    level: str = "",
    task_id: str = "",
    account_id: str = "",
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    page_size = 100
    offset = (page - 1) * page_size

    query = select(TaskLog)
    if module:
        query = query.where(TaskLog.module == module)
    if level:
        query = query.where(TaskLog.level == level)
    if task_id:
        query = query.where(TaskLog.task_id == task_id)
    if account_id:
        query = query.where(TaskLog.account_id == account_id)

    query = query.order_by(desc(TaskLog.timestamp)).limit(page_size).offset(offset)

    result = await db.execute(query)
    logs = result.scalars().all()

    # 获取任务列表和账号列表用于筛选
    tasks_result = await db.execute(select(Task).order_by(Task.name))
    tasks = tasks_result.scalars().all()

    accounts_result = await db.execute(select(Account).order_by(Account.phone))
    accounts = accounts_result.scalars().all()

    return request.app.state.templates.TemplateResponse("logs/view.html", {
        "request": request,
        "logs": logs,
        "tasks": tasks,
        "accounts": accounts,
        "filter_module": module,
        "filter_level": level,
        "filter_task_id": task_id,
        "filter_account_id": account_id,
        "page": page,
        "modules": ["system", "scrape", "invite", "chat", "account", "proxy", "scheduler"],
        "levels": ["DEBUG", "INFO", "WARNING", "ERROR"],
    })


@router.get("/data")
async def logs_data(
    module: str = "",
    level: str = "",
    task_id: str = "",
    account_id: str = "",
    page: int = 1,
    per_page: int = 200,
    db: AsyncSession = Depends(get_db),
):
    """JSON API for fetching logs (used by frontend JS)."""
    offset = (page - 1) * per_page

    query = select(TaskLog)
    count_query = select(func.count(TaskLog.id))

    if module:
        query = query.where(TaskLog.module == module)
        count_query = count_query.where(TaskLog.module == module)
    if level:
        query = query.where(TaskLog.level == level)
        count_query = count_query.where(TaskLog.level == level)
    if task_id:
        query = query.where(TaskLog.task_id == task_id)
        count_query = count_query.where(TaskLog.task_id == task_id)
    if account_id:
        query = query.where(TaskLog.account_id == account_id)
        count_query = count_query.where(TaskLog.account_id == account_id)

    query = query.order_by(desc(TaskLog.timestamp)).limit(per_page).offset(offset)

    result = await db.execute(query)
    logs = result.scalars().all()

    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                "level": log.level,
                "module": log.module or "",
                "message": log.message or "",
                "created_at": log.timestamp.isoformat() if log.timestamp else "",
                "task_id": log.task_id or "",
                "account_id": log.account_id or "",
            }
            for log in logs
        ],
        "total": total,
        "total_pages": total_pages,
        "page": page,
    })


@router.get("/download")
async def download_logs(
    module: str = "",
    level: str = "",
    task_id: str = "",
    account_id: str = "",
    db: AsyncSession = Depends(get_db),
):
    """下载日志为文本文件"""
    query = select(TaskLog)
    if module:
        query = query.where(TaskLog.module == module)
    if level:
        query = query.where(TaskLog.level == level)
    if task_id:
        query = query.where(TaskLog.task_id == task_id)
    if account_id:
        query = query.where(TaskLog.account_id == account_id)

    query = query.order_by(TaskLog.timestamp).limit(10000)

    result = await db.execute(query)
    logs = result.scalars().all()

    def generate():
        for log in logs:
            ts = log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else ""
            yield f"[{ts}] [{log.level}] [{log.module}] {log.message}\n"

    filename = f"logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/stream")
async def log_stream(
    request: Request,
    task_id: str = "",
):
    """SSE 实时日志流 — 发送 JSON 格式数据"""
    last_timestamp = None

    async def event_generator():
        nonlocal last_timestamp
        while True:
            query = select(TaskLog).order_by(desc(TaskLog.timestamp)).limit(10)
            if task_id:
                query = query.where(TaskLog.task_id == task_id)
            if last_timestamp:
                query = query.where(TaskLog.timestamp > last_timestamp)

            async with async_session_factory() as session:
                result = await session.execute(query)
                logs = list(result.scalars().all())

            if logs:
                last_timestamp = logs[0].timestamp
                for log in reversed(logs):
                    data = json.dumps({
                        "id": log.id,
                        "level": log.level,
                        "module": log.module or "",
                        "message": log.message or "",
                        "created_at": log.timestamp.isoformat() if log.timestamp else "",
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
