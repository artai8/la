"""智能账号调度器 — 100 账号场景的核心防封组件

职责:
- 按轮转策略选择最优账号
- 自动跳过冷却中 / 每日超限 / 新号养号期的账号
- 维护每日计数器重置
- 记录 FloodWait / PeerFlood 并升级冷却
"""
import asyncio
import hashlib
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, DEVICE_FINGERPRINTS
from app.models import Account
from app.database import async_session_factory

logger = logging.getLogger(__name__)

# ─── 全局锁: 防止并发领取同一账号 ───
_schedule_lock = asyncio.Lock()


def _fingerprint_hash(fp: dict) -> str:
    """根据指纹数据生成唯一 hash"""
    raw = f"{fp['device_model']}|{fp['system_version']}|{fp['app_version']}|{fp['lang_code']}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


async def assign_fingerprint(account: Account, db: AsyncSession) -> dict:
    """为账号**持久化**分配一个设备指纹; 已有则直接返回"""
    if account.fingerprint_hash and account.device_model:
        # 已绑定
        return {
            "device_model": account.device_model,
            "system_version": account.system_version,
            "app_version": account.app_version,
            "lang_code": account.lang_code,
            "system_lang_code": account.system_lang_code,
        }

    # 查询已被使用的 fingerprint_hash
    result = await db.execute(
        select(Account.fingerprint_hash).where(Account.fingerprint_hash != "")
    )
    used_hashes = {r[0] for r in result.all()}

    # 优先选未被使用的指纹
    available = [fp for fp in DEVICE_FINGERPRINTS if _fingerprint_hash(fp) not in used_hashes]
    chosen = random.choice(available) if available else random.choice(DEVICE_FINGERPRINTS)

    account.device_model = chosen["device_model"]
    account.system_version = chosen["system_version"]
    account.app_version = chosen["app_version"]
    account.lang_code = chosen["lang_code"]
    account.system_lang_code = chosen["system_lang_code"]
    account.fingerprint_hash = _fingerprint_hash(chosen)
    await db.commit()

    logger.info(f"账号 {account.phone} 绑定指纹: {chosen['device_model']} ({account.fingerprint_hash})")
    return chosen


async def _reset_daily_if_needed(account: Account, now: datetime):
    """如果日期已翻过，重置每日计数器"""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if account.daily_invite_reset_at is None or account.daily_invite_reset_at < today:
        account.daily_invite_count = 0
        account.daily_invite_reset_at = now
    if account.daily_message_reset_at is None or account.daily_message_reset_at < today:
        account.daily_message_count = 0
        account.daily_message_reset_at = now


def _is_in_cooldown(account: Account, now: datetime) -> bool:
    return account.cooldown_until is not None and account.cooldown_until > now


def _is_new_account(account: Account, now: datetime) -> bool:
    if not account.is_new:
        return False
    if account.registered_at and (now - account.registered_at).days >= settings.NEW_ACCOUNT_COOLDOWN_DAYS:
        return False
    return True


def _is_recent_peer_flood(account: Account, now: datetime) -> bool:
    """最近触发过 PeerFlood 的账号临时降权，避免刚解封后再次触发。"""
    details = account.restriction_details or {}
    ts = details.get("last_peer_flood_at") if isinstance(details, dict) else None
    if not ts:
        return False
    try:
        last_pf = datetime.fromisoformat(ts)
    except Exception:
        return False

    shield_seconds = int(getattr(settings, "PEER_FLOOD_RECOVERY_SHIELD", 172800))
    return (now - last_pf).total_seconds() < shield_seconds


async def select_accounts_for_invite(
    candidate_ids: list[str],
    needed: int,
    db: AsyncSession,
) -> list[Account]:
    """
    从候选列表中选出 ≤ needed 个可执行邀请的账号。
    选择策略: 排除冷却/超限/新号 → 按 health_score DESC + last_used_at ASC 排序。
    """
    async with _schedule_lock:
        now = datetime.utcnow()
        result = await db.execute(
            select(Account).where(
                and_(
                    Account.id.in_(candidate_ids),
                    Account.status == "active",
                )
            ).order_by(Account.health_score.desc(), Account.last_used_at.asc().nullsfirst())
        )
        accounts = result.scalars().all()

        eligible = []
        for acc in accounts:
            await _reset_daily_if_needed(acc, now)
            if _is_in_cooldown(acc, now):
                logger.debug(f"跳过冷却中账号: {acc.phone}")
                continue
            if _is_recent_peer_flood(acc, now):
                logger.debug(f"跳过近期 PeerFlood 账号: {acc.phone}")
                continue
            if _is_new_account(acc, now):
                logger.debug(f"跳过新号养号期: {acc.phone}")
                continue
            if acc.daily_invite_count >= settings.DAILY_INVITE_LIMIT:
                logger.debug(f"跳过已达邀请上限: {acc.phone} ({acc.daily_invite_count})")
                continue
            eligible.append(acc)
            if len(eligible) >= needed:
                break

        await db.commit()
        return eligible


async def select_accounts_for_chat(
    candidate_ids: list[str],
    needed: int,
    db: AsyncSession,
) -> list[Account]:
    """选择可执行群发的账号"""
    async with _schedule_lock:
        now = datetime.utcnow()
        result = await db.execute(
            select(Account).where(
                and_(
                    Account.id.in_(candidate_ids),
                    Account.status == "active",
                )
            ).order_by(Account.health_score.desc(), Account.last_used_at.asc().nullsfirst())
        )
        accounts = result.scalars().all()

        eligible = []
        for acc in accounts:
            await _reset_daily_if_needed(acc, now)
            if _is_in_cooldown(acc, now):
                continue
            if _is_new_account(acc, now):
                continue
            if acc.daily_message_count >= settings.DAILY_MESSAGE_LIMIT:
                continue
            eligible.append(acc)
            if len(eligible) >= needed:
                break

        await db.commit()
        return eligible


async def record_flood_wait(account_id: str, wait_seconds: int):
    """
    记录 FloodWait 事件，升级冷却策略:
    第1次 → Telegram 返回的 wait_seconds
    第2次 → 2小时
    第3次+ → 24小时
    """
    async with async_session_factory() as db:
        result = await db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            return

        account.flood_wait_count = (account.flood_wait_count or 0) + 1
        account.health_score = max(0, (account.health_score or 100) - 5)

        fw_count = account.flood_wait_count
        if fw_count <= 1:
            cooldown_secs = max(wait_seconds, settings.COOLDOWN_FLOOD_1)
        elif fw_count == 2:
            cooldown_secs = settings.COOLDOWN_FLOOD_2
        else:
            cooldown_secs = settings.COOLDOWN_FLOOD_3

        account.cooldown_until = datetime.utcnow() + timedelta(seconds=cooldown_secs)
        await db.commit()

        logger.warning(
            f"FloodWait #{fw_count} → 账号 {account.phone} 冷却 {cooldown_secs}s "
            f"(health={account.health_score})"
        )


async def record_peer_flood(account_id: str):
    """PeerFloodError → 阶梯冷却 + 大幅扣分"""
    async with async_session_factory() as db:
        result = await db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            return

        account.peer_flood_count = (account.peer_flood_count or 0) + 1
        pf_count = account.peer_flood_count

        # 累积违规按次数升级: 24h -> 48h -> 72h(封顶)
        cooldown_secs = int(settings.COOLDOWN_PEER_FLOOD)
        if pf_count == 2:
            cooldown_secs = max(cooldown_secs, 48 * 3600)
        elif pf_count >= 3:
            cooldown_secs = max(cooldown_secs, 72 * 3600)

        penalty = min(35, 20 + (pf_count - 1) * 5)
        account.health_score = max(0, (account.health_score or 100) - penalty)
        account.cooldown_until = datetime.utcnow() + timedelta(seconds=cooldown_secs)
        details = account.restriction_details or {}
        if not isinstance(details, dict):
            details = {}
        details.update({
            "last_peer_flood_at": datetime.utcnow().isoformat(),
            "peer_flood_count": pf_count,
            "peer_flood_cooldown_seconds": cooldown_secs,
        })
        account.restriction_details = details
        await db.commit()

        logger.error(
            f"PeerFlood #{pf_count} → 账号 {account.phone} 冷却 {cooldown_secs // 3600}h "
            f"(health={account.health_score})"
        )


async def record_operation(account_id: str, op_type: str = "invite", count: int = 1):
    """记录一次成功操作, 更新 daily 计数和 last_used_at"""
    async with async_session_factory() as db:
        result = await db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            return

        now = datetime.utcnow()
        await _reset_daily_if_needed(account, now)
        account.last_used_at = now

        if op_type == "invite":
            account.daily_invite_count = (account.daily_invite_count or 0) + count
        elif op_type == "chat":
            account.daily_message_count = (account.daily_message_count or 0) + count

        await db.commit()


async def mark_account_registered(account_id: str):
    """首次登录成功后标记注册时间、新号标记"""
    async with async_session_factory() as db:
        result = await db.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            return
        if not account.registered_at:
            account.registered_at = datetime.utcnow()
            account.is_new = True
        await db.commit()
