"""同步服务 - 本地DB ↔ Supabase"""
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Account, ScrapedMember, ScrapedMessage, Group
from app.database import get_supabase

logger = logging.getLogger(__name__)


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
