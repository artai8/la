"""任务管理路由"""
import logging
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func

from app.database import get_db
from app.models import Task, Account, Group, ScrapedMember, TaskLog
from app.services.task_scheduler import register_task, unregister_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_class=HTMLResponse)
async def tasks_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).order_by(Task.created_at.desc()))
    tasks = result.scalars().all()

    acc_result = await db.execute(
        select(Account).where(Account.status == "active").order_by(Account.created_at)
    )
    accounts = acc_result.scalars().all()

    # 获取有已采集成员的群组（供invite/chat类型选择来源群组）
    group_result = await db.execute(
        select(Group, func.count(ScrapedMember.id).label("member_count"))
        .join(ScrapedMember, Group.id == ScrapedMember.group_id, isouter=True)
        .group_by(Group.id)
        .having(func.count(ScrapedMember.id) > 0)
        .order_by(Group.title)
    )
    groups_with_counts = group_result.all()

    return request.app.state.templates.TemplateResponse("tasks/list.html", {
        "request": request,
        "tasks": tasks,
        "accounts": accounts,
        "groups_with_counts": groups_with_counts,
    })


@router.post("/create")
async def create_task(
    request: Request,
    name: str = Form(""),
    task_type: str = Form(...),
    cron_expression: str = Form("0 8 * * *"),
    account_ids: list[str] = Form([]),
    # 任务参数
    group_inputs: str = Form(""),
    target_groups: str = Form(""),
    filter_admins: bool = Form(True),
    filter_bots: bool = Form(True),
    online_filter: str = Form("none"),
    save_local: bool = Form(True),
    save_remote: bool = Form(False),
    message_limit: int = Form(100),
    delay_min: int = Form(300),
    delay_max: int = Form(600),
    concurrency: int = Form(1),
    per_account_limit: int = Form(5),
    source_group_ids: list[str] = Form([]),
    # 流水线专用参数
    invite_delay_min: int = Form(300),
    invite_delay_max: int = Form(600),
    invite_per_account_limit: int = Form(5),
    chat_delay_min: int = Form(300),
    chat_delay_max: int = Form(600),
    chat_per_account_limit: int = Form(10),
    phase_delay: int = Form(60),
    pipeline_concurrency: int = Form(3),
    use_remote_db: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    type_names = {
        "scrape_members": "采集群成员",
        "scrape_messages": "采集聊天内容",
        "invite": "拉人入群",
        "chat": "群聊发送",
        "nurture": "养号",
        "check_restriction": "检测限制",
        "pipeline": "流水线(拉人+群聊)",
    }
    if not name:
        name = type_names.get(task_type, task_type)

    # 根据任务类型构建配置
    if task_type == "pipeline":
        config = {
            "source_group_ids": source_group_ids,
            "target_groups": target_groups,
            "invite_delay_min": invite_delay_min,
            "invite_delay_max": invite_delay_max,
            "invite_per_account_limit": invite_per_account_limit,
            "chat_delay_min": chat_delay_min,
            "chat_delay_max": chat_delay_max,
            "chat_per_account_limit": chat_per_account_limit,
            "phase_delay": phase_delay,
            "pipeline_concurrency": pipeline_concurrency,
            "use_remote_db": use_remote_db,
        }
    else:
        config = {
            "group_inputs": group_inputs,
            "target_groups": target_groups,
            "filter_admins": filter_admins,
            "filter_bots": filter_bots,
            "online_filter": online_filter,
            "save_local": save_local,
            "save_remote": save_remote,
            "message_limit": message_limit,
            "delay_min": delay_min,
            "delay_max": delay_max,
            "concurrency": concurrency,
            "per_account_limit": per_account_limit,
            "source_group_ids": source_group_ids,
        }

    task = Task(
        name=name,
        task_type=task_type,
        config_json=config,
        cron_expression=cron_expression,
        account_ids=account_ids,
        enabled=True,
    )
    db.add(task)
    await db.commit()

    register_task(task.id, cron_expression)

    return RedirectResponse("/tasks", status_code=303)


@router.post("/toggle/{task_id}")
async def toggle_task(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task:
        task.enabled = not task.enabled
        await db.commit()
        if task.enabled:
            register_task(task.id, task.cron_expression)
        else:
            unregister_task(task.id)
    return RedirectResponse("/tasks", status_code=303)


@router.post("/trigger/{task_id}")
async def trigger_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """手动触发任务"""
    from app.services.task_scheduler import _execute_task
    import asyncio

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return JSONResponse({"status": "error", "message": "任务不存在"})

    asyncio.create_task(_execute_task(task_id))
    return JSONResponse({"status": "started", "message": f"任务 '{task.name}' 已手动触发"})


@router.post("/delete/{task_id}")
async def delete_task(task_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import TaskLog
    unregister_task(task_id)
    # 先删除关联的日志记录，避免外键约束冲突
    await db.execute(delete(TaskLog).where(TaskLog.task_id == task_id))
    await db.execute(delete(Task).where(Task.id == task_id))
    await db.commit()
    return RedirectResponse("/tasks", status_code=303)


@router.post("/cancel/{task_id}")
async def cancel_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """取消运行中的任务"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return JSONResponse({"status": "error", "message": "任务不存在"})
    task.is_cancelled = True
    await db.commit()
    return JSONResponse({"status": "ok", "message": f"任务 '{task.name}' 已标记取消"})
