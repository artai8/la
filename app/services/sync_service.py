"""同步服务 - 本地DB ↔ Supabase"""
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Account, ScrapedMember, ScrapedMessage, Group
from app.database import get_supabase

logger = logging.getLogger(__name__)


async def pull_sessions_from_remote(
    db: AsyncSession,
    api_config_id: str,
    proxy_id: str | None = None,
) -> dict:
    """从 Supabase 远程数据库拉取所有 session 并导入到本地

    Returns: {total, imported, skipped, failed, errors: [...], message}
    """
    supabase = get_supabase()
    if not supabase:
        return {"total": 0, "imported": 0, "skipped": 0, "failed": 0,
                "errors": ["Supabase 未配置，请先在设置页面配置远程数据库"],
                "message": "Supabase 未配置"}

    try:
        response = supabase.table("sessions").select("*").execute()
        remote_sessions = response.data if response.data else []
    except Exception as e:
        logger.error(f"从 Supabase 拉取 sessions 失败: {e}")
        return {"total": 0, "imported": 0, "skipped": 0, "failed": 0,
                "errors": [f"拉取失败: {e}"], "message": f"拉取失败: {e}"}

    if not remote_sessions:
        return {"total": 0, "imported": 0, "skipped": 0, "failed": 0,
                "errors": [], "message": "远程数据库无 session 记录"}

    # 延迟导入避免循环依赖
    from app.routers.accounts import _import_single_session

    total = len(remote_sessions)
    imported = 0
    skipped = 0
    failed = 0
    errors = []

    for row in remote_sessions:
        phone = row.get("phone", "").strip()
        session_string = row.get("session_string", "").strip()
        if not phone or not session_string:
            skipped += 1
            continue

        result = await _import_single_session(
            phone=phone,
            session_string=session_string,
            api_config_id=api_config_id,
            proxy_id=proxy_id,
            db=db,
            device_model=row.get("device_model", ""),
            system_version=row.get("system_version", ""),
            app_version=row.get("app_version", ""),
        )

        if result["success"]:
            imported += 1
        elif "跳过" in result["message"] or "已存在" in result["message"]:
            skipped += 1
        else:
            failed += 1
            errors.append(result["message"])

    msg = f"远程拉取完成: 共 {total} 条, 导入 {imported}, 跳过 {skipped}, 失败 {failed}"
    logger.info(msg)
    return {
        "total": total,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:20],
        "message": msg,
    }


async def sync_session_to_remote(db: AsyncSession, account_id: str):
    """将账号的 session 同步到 Supabase"""
    supabase = get_supabase()
    if not supabase:
        return

    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account or not account.session_string:
        return

    try:
        supabase.table("sessions").upsert({
            "phone": account.phone,
            "session_string": account.session_string,
            "device_model": account.device_model,
            "system_version": account.system_version,
            "app_version": account.app_version,
            "status": account.status,
            "nickname": account.nickname,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="phone").execute()
        logger.info(f"Session 已同步到 Supabase: {account.phone}")
    except Exception as e:
        logger.error(f"同步 Session 到 Supabase 失败: {e}")


async def sync_members_to_remote(db: AsyncSession, group_id: str):
    """将群成员同步到 Supabase"""
    supabase = get_supabase()
    if not supabase:
        return

    result = await db.execute(
        select(ScrapedMember).where(ScrapedMember.group_id == group_id)
    )
    members = result.scalars().all()

    group_result = await db.execute(select(Group).where(Group.id == group_id))
    group = group_result.scalar_one_or_none()

    if not group:
        return

    try:
        for m in members:
            supabase.table("scraped_members").upsert({
                "user_id": m.user_id,
                "username": m.username,
                "first_name": m.first_name,
                "last_name": m.last_name,
                "group_telegram_id": group.telegram_id,
                "group_title": group.title,
                "online_status": m.online_status,
                "is_invited": m.is_invited,
                "invite_status": m.invite_status,
                "scraped_at": m.scraped_at.isoformat() if m.scraped_at else None,
            }, on_conflict="user_id,group_telegram_id").execute()
        logger.info(f"已同步 {len(members)} 个成员到 Supabase")
    except Exception as e:
        logger.error(f"同步成员到 Supabase 失败: {e}")


async def sync_messages_to_remote(db: AsyncSession, group_id: str):
    """将聊天内容同步到 Supabase"""
    supabase = get_supabase()
    if not supabase:
        return

    result = await db.execute(
        select(ScrapedMessage).where(ScrapedMessage.group_id == group_id)
    )
    messages = result.scalars().all()

    group_result = await db.execute(select(Group).where(Group.id == group_id))
    group = group_result.scalar_one_or_none()

    if not group:
        return

    try:
        for m in messages:
            supabase.table("scraped_messages").upsert({
                "telegram_msg_id": m.telegram_msg_id,
                "group_telegram_id": group.telegram_id,
                "group_title": group.title,
                "sender_id": m.sender_id,
                "sender_username": m.sender_username,
                "text": m.text,
                "date": m.date.isoformat() if m.date else None,
                "is_sent": m.is_sent,
                "scraped_at": m.scraped_at.isoformat() if m.scraped_at else None,
            }, on_conflict="telegram_msg_id,group_telegram_id").execute()
        logger.info(f"已同步 {len(messages)} 条消息到 Supabase")
    except Exception as e:
        logger.error(f"同步消息到 Supabase 失败: {e}")
