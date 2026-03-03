"""账号管理路由"""
import logging
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Account, TelegramApiConfig, Proxy, Setting
from app.services import telegram_client as tc
from app.services.account_service import nurture_account, check_restriction
from app.services.account_scheduler import assign_fingerprint, mark_account_registered
from app.services.sync_service import sync_session_to_remote
from app.services.proxy_manager import start_xray_process, _allocate_port

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_class=HTMLResponse)
async def accounts_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Account).order_by(Account.created_at.desc())
    )
    accounts = result.scalars().all()

    # 获取 API 配置列表
    api_result = await db.execute(select(TelegramApiConfig))
    api_configs = api_result.scalars().all()

    # 获取代理列表
    proxy_result = await db.execute(select(Proxy).where(Proxy.status != "dead"))
    proxies = proxy_result.scalars().all()

    return request.app.state.templates.TemplateResponse("accounts/list.html", {
        "request": request,
        "accounts": accounts,
        "api_configs": api_configs,
        "proxies": proxies,
    })


@router.post("/send-code")
async def send_code(
    request: Request,
    phone: str = Form(...),
    api_config_id: str = Form(...),
    proxy_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """步骤1: 发送验证码"""
    # 获取 API 配置
    api_result = await db.execute(
        select(TelegramApiConfig).where(TelegramApiConfig.id == api_config_id)
    )
    api_config = api_result.scalar_one_or_none()
    if not api_config:
        return JSONResponse({"status": "error", "message": "API配置不存在"})

    # 查找或创建账号
    acc_result = await db.execute(select(Account).where(Account.phone == phone))
    account = acc_result.scalar_one_or_none()

    if not account:
        account = Account(
            phone=phone,
            api_config_id=api_config_id,
            proxy_id=proxy_id if proxy_id else None,
        )
        db.add(account)
        await db.flush()
        # 智能分配设备指纹（避免重复）
        fingerprint = await assign_fingerprint(account, db)

    # 如果有代理，启动 xray
    if proxy_id:
        proxy_result = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
        proxy = proxy_result.scalar_one_or_none()
        if proxy:
            if not proxy.local_port:
                proxy.local_port = _allocate_port()
                await db.flush()
            start_xray_process(proxy.id, {
                "protocol": proxy.protocol,
                "address": proxy.address,
                "port": proxy.port,
                "config_json": proxy.config_json,
            }, proxy.local_port)

    try:
        phone_code_hash = await tc.send_code(
            account_id=account.id,
            phone=phone,
            api_id=api_config.api_id,
            api_hash=api_config.api_hash,
            device_model=account.device_model,
            system_version=account.system_version,
            app_version=account.app_version,
            lang_code=account.lang_code,
            system_lang_code=account.system_lang_code,
            proxy_id=proxy_id if proxy_id else None,
        )
        account.phone_code_hash = phone_code_hash
        await db.commit()
        return JSONResponse({
            "status": "code_sent",
            "account_id": account.id,
            "message": "验证码已发送",
        })
    except Exception as e:
        logger.error(f"发送验证码失败: {e}")
        return JSONResponse({"status": "error", "message": str(e)})


@router.post("/verify-code")
async def verify_code(
    request: Request,
    account_id: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """步骤2: 验证码验证"""
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if not account:
        return JSONResponse({"status": "error", "message": "账号不存在"})

    result = await tc.sign_in_with_code(
        account_id=account.id,
        phone=account.phone,
        code=code,
        phone_code_hash=account.phone_code_hash,
    )

    if result["status"] == "ok":
        account.session_string = result["session_string"]
        account.status = "active"
        await db.commit()
        # 标记首次登录
        await mark_account_registered(account.id)
        # 同步到远程
        await sync_session_to_remote(db, account.id)
        return JSONResponse({"status": "ok", "message": "登录成功"})
    elif result["status"] == "2fa_required":
        account.status = "2fa_required"
        await db.commit()
        return JSONResponse({"status": "2fa_required", "account_id": account.id})
    else:
        return JSONResponse(result)


@router.post("/verify-2fa")
async def verify_2fa(
    request: Request,
    account_id: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """步骤3: 2FA验证"""
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if not account:
        return JSONResponse({"status": "error", "message": "账号不存在"})

    result = await tc.sign_in_with_2fa(account.id, password)

    if result["status"] == "ok":
        account.session_string = result["session_string"]
        account.status = "active"
        account.two_fa_password = password
        await db.commit()
        # 标记首次登录
        await mark_account_registered(account.id)
        await sync_session_to_remote(db, account.id)
        return JSONResponse({"status": "ok", "message": "2FA登录成功"})
    else:
        return JSONResponse(result)


@router.post("/nickname")
async def set_nickname(
    request: Request,
    account_id: str = Form(...),
    nickname: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """设置账号备注"""
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if account:
        account.nickname = nickname
        await db.commit()
    return RedirectResponse("/accounts", status_code=303)


@router.post("/nurture/{account_id}")
async def nurture(account_id: str, db: AsyncSession = Depends(get_db)):
    """一键养号"""
    # 确保客户端已连接
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if not account or not account.session_string:
        return JSONResponse({"success": False, "message": "账号未登录"})

    # 尝试恢复 session
    if not tc.get_client(account_id):
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

    result = await nurture_account(account_id)
    return JSONResponse(result)


@router.post("/check-restriction/{account_id}")
async def check_restriction_route(account_id: str, db: AsyncSession = Depends(get_db)):
    """一键检测双向限制"""
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if not account or not account.session_string:
        return JSONResponse({"restricted": None, "details": {"error": "账号未登录"}})

    if not tc.get_client(account_id):
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

    result = await check_restriction(account_id)
    if result["restricted"] is not None:
        account.is_restricted = result["restricted"]
        await db.commit()
    return JSONResponse(result)


@router.post("/delete/{account_id}")
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    """删除账号"""
    await tc.disconnect_client(account_id)
    acc_result = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_result.scalar_one_or_none()
    if account:
        await db.delete(account)
        await db.commit()
    return RedirectResponse("/accounts", status_code=303)
