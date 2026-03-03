"""采集路由 - 群成员 / 聊天内容"""
import asyncio
import logging
from fastapi import APIRouter, Depends, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db, async_session_factory
from app.models import Account, Group, ScrapedMember, ScrapedMessage, KeywordBlacklist, TelegramApiConfig
from app.services.scrape_service import scrape_group_members, scrape_group_messages
from app.services import telegram_client as tc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scraping", tags=["scraping"])


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


# ==================== 采集群成员 ====================

@router.get("/members", response_class=HTMLResponse)
async def members_page(request: Request, db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(
        select(Account).where(Account.status == "active").order_by(Account.created_at)
    )
    accounts = acc_result.scalars().all()

    group_result = await db.execute(select(Group).order_by(Group.last_scraped_at.desc()))
    groups = group_result.scalars().all()

    return request.app.state.templates.TemplateResponse("scraping/members.html", {
        "request": request,
        "accounts": accounts,
        "groups": groups,
    })


@router.post("/members/start")
async def start_scrape_members(
    request: Request,
    background_tasks: BackgroundTasks,
    group_inputs: str = Form(""),
    account_ids: list[str] = Form([]),
    filter_admins: bool = Form(True),
    filter_bots: bool = Form(True),
    online_filter: str = Form("none"),
    save_local: bool = Form(True),
    save_remote: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    if not group_inputs.strip():
        return JSONResponse({"status": "error", "message": "请输入群组"})
    if not account_ids:
        return JSONResponse({"status": "error", "message": "请选择执行账号"})

    await _ensure_clients(account_ids, db)

    group_list = [g.strip() for g in group_inputs.strip().splitlines() if g.strip()]

    async def _run():
        async with async_session_factory() as session:
            all_results = []
            for aid in account_ids:
                result = await scrape_group_members(
                    account_id=aid,
                    group_identifiers=group_list,
                    db=session,
                    filter_admins=filter_admins,
                    filter_bots=filter_bots,
                    online_filter=online_filter,
                    save_local=save_local,
                    save_remote=save_remote,
                )
                all_results.append(result)

    background_tasks.add_task(_run)
    return JSONResponse({
        "status": "started",
        "message": f"采集任务已启动, 使用 {len(account_ids)} 个账号采集 {len(group_list)} 个群"
    })


@router.get("/members/data", response_class=HTMLResponse)
async def members_data(request: Request, group_id: str = "", db: AsyncSession = Depends(get_db)):
    """获取已采集的群成员数据 (HTMX 片段)"""
    query = select(ScrapedMember)
    if group_id:
        query = query.where(ScrapedMember.group_id == group_id)
    query = query.order_by(ScrapedMember.scraped_at.desc()).limit(500)

    result = await db.execute(query)
    members = result.scalars().all()

    return request.app.state.templates.TemplateResponse("scraping/_members_table.html", {
        "request": request,
        "members": members,
    })


# ==================== 采集聊天内容 ====================

@router.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request, db: AsyncSession = Depends(get_db)):
    acc_result = await db.execute(
        select(Account).where(Account.status == "active").order_by(Account.created_at)
    )
    accounts = acc_result.scalars().all()

    group_result = await db.execute(select(Group).order_by(Group.last_scraped_at.desc()))
    groups = group_result.scalars().all()

    kw_result = await db.execute(select(KeywordBlacklist).order_by(KeywordBlacklist.created_at.desc()))
    keywords = kw_result.scalars().all()

    return request.app.state.templates.TemplateResponse("scraping/messages.html", {
        "request": request,
        "accounts": accounts,
        "groups": groups,
        "keywords": keywords,
    })


@router.post("/messages/start")
async def start_scrape_messages(
    request: Request,
    background_tasks: BackgroundTasks,
    group_inputs: str = Form(""),
    account_ids: list[str] = Form([]),
    filter_admins: bool = Form(True),
    filter_bots: bool = Form(True),
    save_local: bool = Form(True),
    save_remote: bool = Form(False),
    message_limit: int = Form(100),
    db: AsyncSession = Depends(get_db),
):
    if not group_inputs.strip():
        return JSONResponse({"status": "error", "message": "请输入群组"})
    if not account_ids:
        return JSONResponse({"status": "error", "message": "请选择执行账号"})

    await _ensure_clients(account_ids, db)

    group_list = [g.strip() for g in group_inputs.strip().splitlines() if g.strip()]

    async def _run():
        async with async_session_factory() as session:
            for aid in account_ids:
                await scrape_group_messages(
                    account_id=aid,
                    group_identifiers=group_list,
                    db=session,
                    filter_admins=filter_admins,
                    filter_bots=filter_bots,
                    save_local=save_local,
                    save_remote=save_remote,
                    message_limit=message_limit,
                )

    background_tasks.add_task(_run)
    return JSONResponse({
        "status": "started",
        "message": f"消息采集任务已启动, 使用 {len(account_ids)} 个账号"
    })


# ==================== 关键词黑名单 ====================

@router.post("/keywords/add")
async def add_keyword(keyword: str = Form(""), db: AsyncSession = Depends(get_db)):
    keyword = keyword.strip()
    if keyword:
        existing = await db.execute(
            select(KeywordBlacklist).where(KeywordBlacklist.keyword == keyword)
        )
        if not existing.scalar_one_or_none():
            db.add(KeywordBlacklist(keyword=keyword))
            await db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/keywords/batch")
async def batch_add_keywords(raw_text: str = Form(""), db: AsyncSession = Depends(get_db)):
    added = 0
    for line in raw_text.strip().splitlines():
        kw = line.strip()
        if not kw:
            continue
        existing = await db.execute(
            select(KeywordBlacklist).where(KeywordBlacklist.keyword == kw)
        )
        if not existing.scalar_one_or_none():
            db.add(KeywordBlacklist(keyword=kw))
            added += 1
    await db.commit()
    return JSONResponse({"status": "ok", "added": added})


@router.post("/keywords/delete/{keyword_id}")
async def delete_keyword(keyword_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(KeywordBlacklist).where(KeywordBlacklist.id == keyword_id))
    await db.commit()
    return JSONResponse({"status": "ok"})
