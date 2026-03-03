"""设置路由 - Supabase / Telegram API / 代理"""
import logging
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db, get_supabase, reset_supabase
from app.models import Setting, TelegramApiConfig, Proxy
from app.services.proxy_manager import (
    parse_proxy_link, parse_subscription, batch_check_proxies,
    start_xray_process, stop_xray_process, _allocate_port,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


# ==================== Supabase 配置 ====================

@router.get("/supabase", response_class=HTMLResponse)
async def supabase_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Setting).where(Setting.key.in_(["supabase_url", "supabase_key"]))
    )
    settings = {s.key: s.value for s in result.scalars().all()}
    return request.app.state.templates.TemplateResponse("settings/supabase.html", {
        "request": request,
        "supabase_url": settings.get("supabase_url", ""),
        "supabase_key": settings.get("supabase_key", ""),
    })


@router.post("/supabase")
async def save_supabase(
    request: Request,
    supabase_url: str = Form(""),
    supabase_key: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    for key, value in [("supabase_url", supabase_url), ("supabase_key", supabase_key)]:
        result = await db.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            db.add(Setting(key=key, value=value))
    await db.commit()

    # 测试连接
    message = "配置已保存"
    msg_type = "success"
    if supabase_url and supabase_key:
        try:
            reset_supabase()
            client = get_supabase(supabase_url, supabase_key)
            if client:
                message = "配置已保存，连接测试成功"
            else:
                message = "配置已保存，但连接测试失败"
                msg_type = "warning"
        except Exception as e:
            message = f"配置已保存，但连接失败: {e}"
            msg_type = "error"

    return request.app.state.templates.TemplateResponse("settings/supabase.html", {
        "request": request,
        "supabase_url": supabase_url,
        "supabase_key": supabase_key,
        "message": message,
        "msg_type": msg_type,
    })


# ==================== Telegram API 配置 ====================

@router.get("/telegram-api", response_class=HTMLResponse)
async def telegram_api_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TelegramApiConfig).order_by(TelegramApiConfig.created_at.desc()))
    configs = result.scalars().all()
    return request.app.state.templates.TemplateResponse("settings/telegram_api.html", {
        "request": request,
        "configs": configs,
    })


@router.post("/telegram-api/add")
async def add_telegram_api(
    request: Request,
    api_id: int = Form(...),
    api_hash: str = Form(...),
    label: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    config = TelegramApiConfig(api_id=api_id, api_hash=api_hash, label=label)
    db.add(config)
    await db.commit()
    return RedirectResponse("/settings/telegram-api", status_code=303)


@router.post("/telegram-api/batch")
async def batch_add_telegram_api(
    request: Request,
    raw_text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    added = 0
    errors = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 2:
            try:
                api_id = int(parts[0].strip())
                api_hash = parts[1].strip()
                label = parts[2].strip() if len(parts) > 2 else ""
                db.add(TelegramApiConfig(api_id=api_id, api_hash=api_hash, label=label))
                added += 1
            except ValueError:
                errors.append(f"无效行: {line}")
        else:
            errors.append(f"格式错误: {line}")
    await db.commit()

    result = await db.execute(select(TelegramApiConfig).order_by(TelegramApiConfig.created_at.desc()))
    configs = result.scalars().all()
    return request.app.state.templates.TemplateResponse("settings/telegram_api.html", {
        "request": request,
        "configs": configs,
        "message": f"成功添加 {added} 条" + (f", {len(errors)} 条失败" if errors else ""),
        "msg_type": "success" if not errors else "warning",
    })


@router.post("/telegram-api/delete/{config_id}")
async def delete_telegram_api(config_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(TelegramApiConfig).where(TelegramApiConfig.id == config_id))
    await db.commit()
    return RedirectResponse("/settings/telegram-api", status_code=303)


# ==================== 代理配置 ====================

@router.get("/proxy", response_class=HTMLResponse)
async def proxy_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Proxy).order_by(Proxy.created_at.desc()))
    proxies = result.scalars().all()
    return request.app.state.templates.TemplateResponse("settings/proxy.html", {
        "request": request,
        "proxies": proxies,
    })


@router.post("/proxy/add")
async def add_proxy(
    request: Request,
    raw_link: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    try:
        parsed = parse_proxy_link(raw_link)
        proxy = Proxy(
            protocol=parsed["protocol"],
            raw_link=raw_link,
            address=parsed["address"],
            port=parsed["port"],
            config_json=parsed["config_json"],
        )
        db.add(proxy)
        await db.commit()
        message = "代理添加成功"
        msg_type = "success"
    except Exception as e:
        message = f"解析失败: {e}"
        msg_type = "error"

    result = await db.execute(select(Proxy).order_by(Proxy.created_at.desc()))
    proxies = result.scalars().all()
    return request.app.state.templates.TemplateResponse("settings/proxy.html", {
        "request": request,
        "proxies": proxies,
        "message": message,
        "msg_type": msg_type,
    })


@router.post("/proxy/batch")
async def batch_add_proxy(
    request: Request,
    raw_text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    added = 0
    errors = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = parse_proxy_link(line)
            proxy = Proxy(
                protocol=parsed["protocol"],
                raw_link=line,
                address=parsed["address"],
                port=parsed["port"],
                config_json=parsed["config_json"],
            )
            db.add(proxy)
            added += 1
        except Exception as e:
            errors.append(f"{line[:30]}...: {e}")
    await db.commit()

    result = await db.execute(select(Proxy).order_by(Proxy.created_at.desc()))
    proxies = result.scalars().all()
    return request.app.state.templates.TemplateResponse("settings/proxy.html", {
        "request": request,
        "proxies": proxies,
        "message": f"成功添加 {added} 条" + (f", {len(errors)} 条失败" if errors else ""),
        "msg_type": "success" if not errors else "warning",
    })


@router.post("/proxy/subscription")
async def import_subscription(
    request: Request,
    subscription_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    try:
        parsed_list = await parse_subscription(subscription_url)
        added = 0
        for parsed in parsed_list:
            proxy = Proxy(
                protocol=parsed["protocol"],
                raw_link=parsed["raw_link"],
                address=parsed["address"],
                port=parsed["port"],
                config_json=parsed["config_json"],
                subscription_url=subscription_url,
            )
            db.add(proxy)
            added += 1
        await db.commit()
        message = f"从订阅导入 {added} 个代理"
        msg_type = "success"
    except Exception as e:
        message = f"订阅导入失败: {e}"
        msg_type = "error"

    result = await db.execute(select(Proxy).order_by(Proxy.created_at.desc()))
    proxies = result.scalars().all()
    return request.app.state.templates.TemplateResponse("settings/proxy.html", {
        "request": request,
        "proxies": proxies,
        "message": message,
        "msg_type": msg_type,
    })


@router.post("/proxy/check-all")
async def check_all_proxies(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Proxy))
    proxies = result.scalars().all()

    proxy_dicts = [
        {
            "id": p.id,
            "protocol": p.protocol,
            "address": p.address,
            "port": p.port,
            "config_json": p.config_json,
        }
        for p in proxies
    ]

    check_results = await batch_check_proxies(proxy_dicts)

    # 更新状态
    for p in proxies:
        is_alive = check_results.get(p.id)
        if is_alive is not None:
            p.status = "active" if is_alive else "dead"
    await db.commit()

    # 重新查询
    result = await db.execute(select(Proxy).order_by(Proxy.created_at.desc()))
    proxies = result.scalars().all()

    alive_count = sum(1 for r in check_results.values() if r)
    dead_count = sum(1 for r in check_results.values() if not r)

    return request.app.state.templates.TemplateResponse("settings/proxy.html", {
        "request": request,
        "proxies": proxies,
        "message": f"检测完成: {alive_count} 个存活, {dead_count} 个失效",
        "msg_type": "success",
    })


@router.post("/proxy/delete/{proxy_id}")
async def delete_proxy(proxy_id: str, db: AsyncSession = Depends(get_db)):
    stop_xray_process(proxy_id)
    await db.execute(delete(Proxy).where(Proxy.id == proxy_id))
    await db.commit()
    return RedirectResponse("/settings/proxy", status_code=303)
