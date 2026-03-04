"""采集服务 - 群成员采集 / 聊天内容采集"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from telethon.tl.types import (
    UserStatusRecently,
    UserStatusLastWeek,
    UserStatusLastMonth,
    UserStatusOffline,
    UserStatusOnline,
    ChannelParticipantAdmin,
    ChannelParticipantCreator,
    User,
    Channel,
    Chat,
)
from telethon.tl.functions.channels import GetParticipantsRequest, GetFullChannelRequest
from telethon.tl.types import ChannelParticipantsSearch
from telethon.errors import FloodWaitError, ChatAdminRequiredError, ChannelPrivateError

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Group, ScrapedMember, ScrapedMessage, TaskLog, KeywordBlacklist
from app.services.telegram_client import get_client
from app.database import get_supabase

logger = logging.getLogger(__name__)


def parse_group_identifier(text: str) -> str:
    """
    解析群组标识:
    - '-100xxx' -> 直接返回
    - 't.me/xxx' -> 提取 username
    - 'https://t.me/xxx' -> 提取 username
    - '@xxx' -> 提取 username
    - 'xxx' -> 直接返回 (作为 username)
    """
    text = text.strip()
    if text.startswith("-100"):
        return text  # telegram ID
    match = re.search(r"t\.me/(?:\+)?(\w+)", text)
    if match:
        return match.group(1)
    if text.startswith("@"):
        return text[1:]
    return text


async def resolve_group(client, identifier: str):
    """解析群组标识为 Telegram 实体"""
    parsed = parse_group_identifier(identifier)
    try:
        if parsed.startswith("-100") or parsed.lstrip("-").isdigit():
            entity = await client.get_entity(int(parsed))
        else:
            entity = await client.get_entity(parsed)
        return entity
    except Exception as e:
        logger.error(f"无法解析群组 '{identifier}': {e}")
        return None


def _get_online_status(user) -> tuple[str, datetime | None]:
    """获取用户在线状态和最后上线时间"""
    status = getattr(user, "status", None)
    if isinstance(status, UserStatusOnline):
        return "online", datetime.now(timezone.utc).replace(tzinfo=None)
    elif isinstance(status, UserStatusRecently):
        return "recently", None
    elif isinstance(status, UserStatusOffline):
        return "offline", status.was_online.replace(tzinfo=None) if status.was_online else None
    elif isinstance(status, UserStatusLastWeek):
        return "last_week", None
    elif isinstance(status, UserStatusLastMonth):
        return "last_month", None
    else:
        return "unknown", None


def _should_filter_by_online(online_status: str, last_online: datetime | None, filter_value: str) -> bool:
    """根据在线过滤条件判断是否应该过滤掉此用户"""
    if filter_value == "none":
        return False  # 不过滤

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    thresholds = {
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }

    threshold = thresholds.get(filter_value)
    if not threshold:
        return False

    # 有精确时间的情况
    if last_online:
        return (now - last_online) > threshold

    # 没有精确时间, 根据状态估算
    status_map = {
        "online": timedelta(0),
        "recently": timedelta(days=1),
        "last_week": timedelta(days=7),
        "last_month": timedelta(days=30),
        "unknown": timedelta(days=365),
    }
    estimated = status_map.get(online_status, timedelta(days=365))
    return estimated > threshold


async def scrape_group_members(
    account_id: str,
    group_identifiers: list[str],
    db: AsyncSession,
    filter_admins: bool = True,
    filter_bots: bool = True,
    online_filter: str = "none",
    save_local: bool = True,
    save_remote: bool = False,
    task_id: str | None = None,
) -> dict:
    """
    采集多个群的成员
    返回: {"total_scraped": N, "groups_processed": N, "errors": [...]}
    """
    client = get_client(account_id)
    if not client:
        return {"total_scraped": 0, "groups_processed": 0, "errors": ["客户端未连接"]}

    total_scraped = 0
    groups_processed = 0
    errors = []

    for identifier in group_identifiers:
        identifier = identifier.strip()
        if not identifier:
            continue

        try:
            entity = await resolve_group(client, identifier)
            if not entity:
                errors.append(f"无法解析群组: {identifier}")
                continue

            # 获取群信息
            telegram_id = entity.id
            if hasattr(entity, "megagroup") or isinstance(entity, Channel):
                telegram_id = int(f"-100{entity.id}")

            title = getattr(entity, "title", "")
            username = getattr(entity, "username", "") or ""

            # 更新或创建群组记录
            group_result = await db.execute(
                select(Group).where(Group.telegram_id == telegram_id)
            )
            group_obj = group_result.scalar_one_or_none()
            if not group_obj:
                group_obj = Group(
                    telegram_id=telegram_id,
                    username=username,
                    title=title,
                    group_type="supergroup" if hasattr(entity, "megagroup") else "group",
                )
                db.add(group_obj)
                await db.flush()

            group_obj.title = title
            group_obj.username = username
            group_obj.last_scraped_at = datetime.utcnow()

            await _log(db, task_id, account_id, "scrape", "INFO",
                       f"开始采集群 '{title}' ({identifier}) 的成员")

            # 获取所有成员
            members_data = []
            try:
                participants = await client.get_participants(entity, aggressive=True)
            except ChatAdminRequiredError:
                errors.append(f"群 '{title}' 需要管理员权限")
                await _log(db, task_id, account_id, "scrape", "ERROR",
                           f"群 '{title}' 需要管理员权限才能获取成员列表")
                continue
            except ChannelPrivateError:
                errors.append(f"群 '{title}' 是私有群组")
                await _log(db, task_id, account_id, "scrape", "ERROR",
                           f"群 '{title}' 是私有群组，无法访问")
                continue

            group_obj.member_count = len(participants)

            for user in participants:
                if not isinstance(user, User):
                    continue

                # 过滤机器人
                if filter_bots and user.bot:
                    continue

                # 过滤管理员
                if filter_admins and hasattr(user, "participant"):
                    participant = user.participant
                    if isinstance(participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
                        continue

                # 在线状态过滤
                online_status, last_online = _get_online_status(user)
                if _should_filter_by_online(online_status, last_online, online_filter):
                    continue

                member_data = {
                    "user_id": user.id,
                    "username": user.username or "",
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "phone": user.phone or "",
                    "group_id": group_obj.id,
                    "last_online": last_online,
                    "online_status": online_status,
                    "is_admin": False,
                    "is_bot": user.bot or False,
                    "scraped_by": account_id,
                }
                members_data.append(member_data)

            # 批量保存到本地数据库
            if save_local and members_data:
                for md in members_data:
                    existing = await db.execute(
                        select(ScrapedMember).where(
                            and_(
                                ScrapedMember.user_id == md["user_id"],
                                ScrapedMember.group_id == md["group_id"],
                            )
                        )
                    )
                    existing_member = existing.scalar_one_or_none()
                    if existing_member:
                        # 更新
                        existing_member.username = md["username"]
                        existing_member.first_name = md["first_name"]
                        existing_member.last_name = md["last_name"]
                        existing_member.online_status = md["online_status"]
                        existing_member.last_online = md["last_online"]
                        existing_member.scraped_by = account_id
                        existing_member.scraped_at = datetime.utcnow()
                    else:
                        db.add(ScrapedMember(**md))

                await db.commit()

            # 保存到远程数据库
            if save_remote and members_data:
                supabase = get_supabase()
                if supabase:
                    try:
                        for md in members_data:
                            remote_data = {
                                "user_id": md["user_id"],
                                "username": md["username"],
                                "first_name": md["first_name"],
                                "last_name": md["last_name"],
                                "phone": md["phone"],
                                "group_telegram_id": telegram_id,
                                "group_title": title,
                                "online_status": md["online_status"],
                                "scraped_at": datetime.utcnow().isoformat(),
                            }
                            supabase.table("scraped_members").upsert(
                                remote_data,
                                on_conflict="user_id,group_telegram_id"
                            ).execute()
                    except Exception as e:
                        logger.error(f"Supabase 保存失败: {e}")
                        errors.append(f"远程保存失败: {e}")

            total_scraped += len(members_data)
            groups_processed += 1

            await _log(db, task_id, account_id, "scrape", "INFO",
                       f"群 '{title}' 采集完成: {len(members_data)} 个成员")

            # 避免 FloodWait
            await asyncio.sleep(2)

        except FloodWaitError as e:
            msg = f"FloodWait: 需要等待 {e.seconds} 秒"
            errors.append(msg)
            await _log(db, task_id, account_id, "scrape", "WARNING", msg)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            msg = f"采集群 '{identifier}' 失败: {e}"
            errors.append(msg)
            logger.error(msg)
            await _log(db, task_id, account_id, "scrape", "ERROR", msg)

    return {
        "total_scraped": total_scraped,
        "groups_processed": groups_processed,
        "errors": errors,
    }


async def scrape_group_messages(
    account_id: str,
    group_identifiers: list[str],
    db: AsyncSession,
    filter_admins: bool = True,
    filter_bots: bool = True,
    save_local: bool = True,
    save_remote: bool = False,
    message_limit: int = 100,
    task_id: str | None = None,
) -> dict:
    """采集多个群的聊天内容"""
    client = get_client(account_id)
    if not client:
        return {"total_scraped": 0, "groups_processed": 0, "errors": ["客户端未连接"]}

    # 加载关键词黑名单
    blacklist_result = await db.execute(select(KeywordBlacklist))
    blacklist = [kw.keyword.lower() for kw in blacklist_result.scalars().all()]

    total_scraped = 0
    groups_processed = 0
    errors = []

    for identifier in group_identifiers:
        identifier = identifier.strip()
        if not identifier:
            continue

        try:
            entity = await resolve_group(client, identifier)
            if not entity:
                errors.append(f"无法解析群组: {identifier}")
                continue

            telegram_id = entity.id
            if hasattr(entity, "megagroup") or isinstance(entity, Channel):
                telegram_id = int(f"-100{entity.id}")

            title = getattr(entity, "title", "")
            username = getattr(entity, "username", "") or ""

            # 更新或创建群组记录
            group_result = await db.execute(
                select(Group).where(Group.telegram_id == telegram_id)
            )
            group_obj = group_result.scalar_one_or_none()
            if not group_obj:
                group_obj = Group(
                    telegram_id=telegram_id,
                    username=username,
                    title=title,
                )
                db.add(group_obj)
                await db.flush()

            await _log(db, task_id, account_id, "scrape", "INFO",
                       f"开始采集群 '{title}' ({identifier}) 的消息")

            messages_data = []
            async for message in client.iter_messages(entity, limit=message_limit):
                if not message.text:
                    continue

                sender = message.sender
                if not sender:
                    continue

                # 过滤机器人
                if filter_bots and hasattr(sender, "bot") and sender.bot:
                    continue

                # 过滤管理员 (简单判断: 检查是否为 Channel 的管理员)
                if filter_admins:
                    try:
                        if hasattr(sender, "participant") and isinstance(
                            sender.participant, (ChannelParticipantAdmin, ChannelParticipantCreator)
                        ):
                            continue
                    except Exception:
                        pass

                # 关键词黑名单过滤
                text_lower = message.text.lower()
                if any(kw in text_lower for kw in blacklist):
                    continue

                msg_data = {
                    "telegram_msg_id": message.id,
                    "group_id": group_obj.id,
                    "sender_id": sender.id if sender else None,
                    "sender_username": getattr(sender, "username", "") or "",
                    "text": message.text,
                    "date": message.date,
                    "scraped_by": account_id,
                }
                messages_data.append(msg_data)

            # 保存到本地数据库
            if save_local and messages_data:
                for md in messages_data:
                    existing = await db.execute(
                        select(ScrapedMessage).where(
                            and_(
                                ScrapedMessage.telegram_msg_id == md["telegram_msg_id"],
                                ScrapedMessage.group_id == md["group_id"],
                            )
                        )
                    )
                    if not existing.scalar_one_or_none():
                        db.add(ScrapedMessage(**md))

                await db.commit()

            # 保存到远程数据库
            if save_remote and messages_data:
                supabase = get_supabase()
                if supabase:
                    try:
                        for md in messages_data:
                            remote_data = {
                                "telegram_msg_id": md["telegram_msg_id"],
                                "group_telegram_id": telegram_id,
                                "group_title": title,
                                "sender_id": md["sender_id"],
                                "sender_username": md["sender_username"],
                                "text": md["text"],
                                "date": md["date"].isoformat() if md["date"] else None,
                                "scraped_at": datetime.utcnow().isoformat(),
                            }
                            supabase.table("scraped_messages").upsert(
                                remote_data,
                                on_conflict="telegram_msg_id,group_telegram_id"
                            ).execute()
                    except Exception as e:
                        logger.error(f"Supabase 保存消息失败: {e}")
                        errors.append(f"远程保存失败: {e}")

            total_scraped += len(messages_data)
            groups_processed += 1

            await _log(db, task_id, account_id, "scrape", "INFO",
                       f"群 '{title}' 消息采集完成: {len(messages_data)} 条")

            await asyncio.sleep(2)

        except FloodWaitError as e:
            msg = f"FloodWait: 需要等待 {e.seconds} 秒"
            errors.append(msg)
            await _log(db, task_id, account_id, "scrape", "WARNING", msg)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            msg = f"采集群 '{identifier}' 消息失败: {e}"
            errors.append(msg)
            logger.error(msg)
            await _log(db, task_id, account_id, "scrape", "ERROR", msg)

    return {
        "total_scraped": total_scraped,
        "groups_processed": groups_processed,
        "errors": errors,
    }


async def _log(db: AsyncSession, task_id: str | None, account_id: str, module: str, level: str, message: str):
    """写日志到数据库"""
    log_entry = TaskLog(
        task_id=task_id,
        account_id=account_id,
        module=module,
        level=level,
        message=message,
    )
    db.add(log_entry)
    await db.commit()
    logger.log(getattr(logging, level, logging.INFO), f"[{module}] {message}")
