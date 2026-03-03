"""拉人与群聊操作路由"""
import logging
from fastapi import APIRouter, Depends, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db, async_session_factory
from app.models import Account, Group, ScrapedMember, ScrapedMessage, TelegramApiConfig
from app.services.invite_service import invite_members
from app.services.chat_service import send_messages
from app.services.account_scheduler import select_accounts_for_invite, select_accounts_for_chat
from app.services import telegram_client as tc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/operations", tags=["operations"])


async def _ensure_clients(account_ids: list[str], db: AsyncSession):
    """确保选中的账号客户端已连接"""
    for aid in account_ids:
        if tc.get_client(aid):
            continue
        acc_result = await db.execute(select(Account).where(Account.id == aid))
        account = acc_result.scalar_one_or_none()
        if not account or not account.session_string:
            continue
        api_result = await db.execute(
            select(TelegramApiConfig).where(TelegramApiConfig.id == account.api_config_id)
        )
        api_config = api_result.scalar_one_or_none()
        if api_config:
            await tc.restore_session(
                account.id, api_config.api_id, api_config.api_hash,
                account.session_string, account.device_model,
                account.system_version, account.app_version,
                account.lang_code, account.system_lang_code,
                account.proxy_id,
            )


# ==================== 拉人入群 ====================

@router.get("/invite", response_class=HTMLResponse)
async def invite_page(request: Request, db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(
        select(Account).where(Account.status == "active").order_by(Account.created_at)
    )
    accounts = acc_result.scalars().all()

    # 获取有已采集成员的群组
    group_result = await db.execute(
        select(Group, func.count(ScrapedMember.id).label("member_count"))
        .join(ScrapedMember, Group.id == ScrapedMember.group_id, isouter=True)
        .group_by(Group.id)
        .having(func.count(ScrapedMember.id) > 0)
        .order_by(Group.title)
    )
    groups_with_counts = group_result.all()

    return request.app.state.templates.TemplateResponse("operations/invite.html", {
        "request": request,
        "accounts": accounts,
        "groups_with_counts": groups_with_counts,
    })


@router.post("/invite/start")
async def start_invite(
    request: Request,
    background_tasks: BackgroundTasks,
    source_group_ids: list[str] = Form([]),
    target_groups: str = Form(""),
    account_ids: list[str] = Form([]),
    delay_min: int = Form(300),
    delay_max: int = Form(600),
    concurrency: int = Form(1),
    per_account_limit: int = Form(5),
    use_remote_db: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    if not source_group_ids:
        return JSONResponse({"status": "error", "message": "请选择来源群组"})
    if not target_groups.strip():
        return JSONResponse({"status": "error", "message": "请输入目标群组"})
    if not account_ids:
        return JSONResponse({"status": "error", "message": "请选择执行账号"})

    # 智能筛选可用账号（排除冷却/超限/新号）
    eligible_accounts = await select_accounts_for_invite(account_ids, per_account_limit * len(account_ids), db)
    filtered_ids = [acc.id for acc in eligible_accounts] if eligible_accounts else account_ids
    if not filtered_ids:
        return JSONResponse({"status": "error", "message": "所有选中账号均在冷却/超限中"})

    await _ensure_clients(filtered_ids, db)

    target_list = [g.strip() for g in target_groups.strip().splitlines() if g.strip()]

    async def _run():
        async with async_session_factory() as session:
            result = await invite_members(
                account_ids=filtered_ids,
                source_group_ids=source_group_ids,
                target_group_inputs=target_list,
                db=session,
                delay_min=delay_min,
                delay_max=delay_max,
                concurrency=concurrency,
                per_account_limit=per_account_limit,
                use_remote_db=use_remote_db,
            )
            logger.info(f"拉人任务完成: {result}")

    background_tasks.add_task(_run)

    return JSONResponse({
        "status": "started",
        "message": f"拉人任务已启动: {len(filtered_ids)}/{len(account_ids)} 个账号可用, 并发 {concurrency}, "
                   f"每账号 {per_account_limit} 人"
    })


# ==================== 群聊发送 ====================

@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(
        select(Account).where(Account.status == "active").order_by(Account.created_at)
    )
    accounts = acc_result.scalars().all()

    # 获取有已采集消息的群组
    group_result = await db.execute(
        select(Group, func.count(ScrapedMessage.id).label("msg_count"))
        .join(ScrapedMessage, Group.id == ScrapedMessage.group_id, isouter=True)
        .group_by(Group.id)
        .having(func.count(ScrapedMessage.id) > 0)
        .order_by(Group.title)
    )
    groups_with_counts = group_result.all()

    return request.app.state.templates.TemplateResponse("operations/chat.html", {
        "request": request,
        "accounts": accounts,
        "groups_with_counts": groups_with_counts,
    })


@router.post("/chat/start")
async def start_chat(
    request: Request,
    background_tasks: BackgroundTasks,
    source_group_ids: list[str] = Form([]),
    target_groups: str = Form(""),
    account_ids: list[str] = Form([]),
    delay_min: int = Form(300),
    delay_max: int = Form(600),
    concurrency: int = Form(5),
    per_account_limit: int = Form(10),
    db: AsyncSession = Depends(get_db),
):
    if not source_group_ids:
        return JSONResponse({"status": "error", "message": "请选择来源群组"})
    if not target_groups.strip():
        return JSONResponse({"status": "error", "message": "请输入目标群组"})
    if not account_ids:
        return JSONResponse({"status": "error", "message": "请选择执行账号"})

    # 智能筛选可用账号
    eligible_accounts = await select_accounts_for_chat(account_ids, per_account_limit * len(account_ids), db)
    filtered_ids = [acc.id for acc in eligible_accounts] if eligible_accounts else account_ids
    if not filtered_ids:
        return JSONResponse({"status": "error", "message": "所有选中账号均在冷却/超限中"})

    await _ensure_clients(filtered_ids, db)

    target_list = [g.strip() for g in target_groups.strip().splitlines() if g.strip()]

    async def _run():
        async with async_session_factory() as session:
            result = await send_messages(
                account_ids=filtered_ids,
                source_group_ids=source_group_ids,
                target_group_inputs=target_list,
                db=session,
                delay_min=delay_min,
                delay_max=delay_max,
                concurrency=concurrency,
                per_account_limit=per_account_limit,
            )
            logger.info(f"群聊发送任务完成: {result}")

    background_tasks.add_task(_run)

    return JSONResponse({
        "status": "started",
        "message": f"群聊任务已启动: {len(filtered_ids)}/{len(account_ids)} 个账号可用, 并发 {concurrency}, "
                   f"每账号 {per_account_limit} 条"
    })
