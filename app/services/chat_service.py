"""群聊发送服务 — 支持100账号并发安全 + 防封"""
import asyncio
import logging
import random
from datetime import datetime

from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    SlowModeWaitError,
    PeerFloodError,
    ChannelPrivateError,
)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update, func
from sqlalchemy.exc import SQLAlchemyError

from app.models import ScrapedMessage, Group, TaskLog
from app.services.telegram_client import get_client, touch_client, ensure_client_connected
from app.services.scrape_service import resolve_group
from app.services.circuit_breaker import circuit_breaker
from app.services.account_scheduler import record_operation, record_flood_wait, record_peer_flood
from app.database import async_session_factory

logger = logging.getLogger(__name__)


async def _log(db: AsyncSession, task_id: str | None, account_id: str, level: str, message: str):
    log_entry = TaskLog(
        task_id=task_id,
        account_id=account_id,
        module="chat",
        level=level,
        message=message,
    )
    try:
        db.add(log_entry)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.warning("chat 日志写入数据库失败，已降级到控制台日志")
    logger.log(getattr(logging, level, logging.INFO), f"[chat] {message}")


async def _claim_messages(
    source_group_ids: list[str],
    limit: int,
    account_id: str,
) -> list[dict]:
    """
    用独立事务 + FOR UPDATE SKIP LOCKED 原子领取消息，
    防止多 worker 拿到同一条。
    返回 [{id, text}, ...] 已被标记 is_sent=True 的消息。
    """
    claimed: list[dict] = []
    async with async_session_factory() as session:
        # FOR UPDATE SKIP LOCKED 确保已被其他 worker 锁定的行直接跳过
        stmt = (
            select(ScrapedMessage)
            .where(
                and_(
                    ScrapedMessage.group_id.in_(source_group_ids),
                    ScrapedMessage.is_sent == False,
                )
            )
            .order_by(func.random())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

        for row in rows:
            if row.text:
                row.is_sent = True
                claimed.append({"id": row.id, "text": row.text})

        await session.commit()
    return claimed


async def send_messages(
    account_ids: list[str],
    source_group_ids: list[str],
    target_group_inputs: list[str],
    db: AsyncSession,
    delay_min: int = 300,
    delay_max: int = 600,
    concurrency: int = 5,
    per_account_limit: int = 10,
    task_id: str | None = None,
) -> dict:
    """
    群聊发送
    - 从采集的消息中获取未发送的内容
    - 多账号并发发送到目标群
    - 集成 circuit_breaker + account_scheduler
    """
    # 前置: 检查熔断器
    if circuit_breaker.is_open():
        remaining = circuit_breaker.remaining_seconds()
        logger.warning(f"熔断器已开启, 拒绝执行发送任务, 剩余冷却 {remaining:.0f}s")
        return {
            "total_sent": 0, "total_failed": 0,
            "errors": [f"熔断器已开启, 请等待 {remaining:.0f} 秒"],
            "account_results": {},
        }

    results = {
        "total_sent": 0,
        "total_failed": 0,
        "errors": [],
        "account_results": {},
    }
    results_lock = asyncio.Lock()

    semaphore = asyncio.Semaphore(concurrency)

    async def _worker(account_id: str):
        async with semaphore:
            await _send_worker(
                account_id, source_group_ids, target_group_inputs,
                per_account_limit, delay_min, delay_max,
                task_id, results, results_lock,
            )

    tasks = [asyncio.create_task(_worker(aid)) for aid in account_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

    return results


async def _send_worker(
    account_id: str,
    source_group_ids: list[str],
    target_group_inputs: list[str],
    per_account_limit: int,
    delay_min: int,
    delay_max: int,
    task_id: str | None,
    results: dict,
    results_lock: asyncio.Lock,
):
    """单个账号的群聊发送工作 — 线程安全 + 防封"""
    client = get_client(account_id)
    if not client:
        async with results_lock:
            results["errors"].append(f"账号 {account_id} 未连接")
        return

    async with async_session_factory() as db:
        sent_count = 0
        failed_count = 0

        await _log(db, task_id, account_id, "INFO",
                   f"开始群聊发送任务, 每账号发送 {per_account_limit} 条")

        # 解析目标群组
        target_entities = []
        for tg_input in target_group_inputs:
            tg_input = tg_input.strip()
            if not tg_input:
                continue
            entity = await resolve_group(client, tg_input)
            if entity:
                target_entities.append(entity)
            else:
                await _log(db, task_id, account_id, "ERROR", f"无法解析目标群: {tg_input}")

        if not target_entities:
            await _log(db, task_id, account_id, "ERROR", "没有有效的目标群组")
            return

        # 用 FOR UPDATE SKIP LOCKED 原子领取消息，确保多 worker 不拿同一条
        claimed = await _claim_messages(source_group_ids, per_account_limit, account_id)

        if not claimed:
            await _log(db, task_id, account_id, "INFO", "没有待发送的消息")
            return

        for msg_item in claimed:
            # 每次操作前检查熔断器
            if circuit_breaker.is_open():
                await _log(db, task_id, account_id, "WARNING",
                           "熔断器已开启, 停止发送")
                break

            # 每次操作前确保客户端仍然连接
            client = await ensure_client_connected(account_id, client)
            if not client:
                await _log(db, task_id, account_id, "ERROR",
                           "客户端连接已断开且无法重连, 停止发送")
                async with results_lock:
                    results["errors"].append(f"账号 {account_id} 连接断开")
                break

            text = msg_item["text"]

            success_any = False
            for target_entity in target_entities:
                try:
                    await client.send_message(target_entity, text)
                    success_any = True
                    await _log(db, task_id, account_id, "INFO",
                               f"成功发送消息到 {getattr(target_entity, 'title', '未知')}: "
                               f"{text[:50]}...")
                except ChatWriteForbiddenError:
                    await _log(db, task_id, account_id, "ERROR",
                               f"没有权限在群 {getattr(target_entity, 'title', '')} 中发言")
                except SlowModeWaitError as e:
                    await _log(db, task_id, account_id, "WARNING",
                               f"慢速模式: 需等待 {e.seconds} 秒")
                    await asyncio.sleep(e.seconds)
                    # 重试一次
                    try:
                        await client.send_message(target_entity, text)
                        success_any = True
                    except Exception:
                        pass
                except ChannelPrivateError:
                    await _log(db, task_id, account_id, "ERROR",
                               f"群 {getattr(target_entity, 'title', '')} 是私有群")
                except PeerFloodError:
                    await _log(db, task_id, account_id, "ERROR",
                               "PeerFloodError: 账号被限制, 停止发送")
                    # 记录到 account_scheduler 和 circuit_breaker
                    await record_peer_flood(account_id)
                    await circuit_breaker.record_peer_flood(account_id)
                    async with results_lock:
                        results["errors"].append(f"账号 {account_id} PeerFloodError")
                        results["account_results"][account_id] = {
                            "sent": sent_count, "failed": failed_count
                        }
                    return
                except FloodWaitError as e:
                    await _log(db, task_id, account_id, "WARNING",
                               f"FloodWait: 等待 {e.seconds} 秒")
                    await record_flood_wait(account_id, e.seconds)
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    await _log(db, task_id, account_id, "ERROR",
                               f"发送消息失败: {e}")

            if success_any:
                sent_count += 1
                # 记录到 account_scheduler
                await record_operation(account_id, "chat")
                async with results_lock:
                    results["total_sent"] = results.get("total_sent", 0) + 1
            else:
                failed_count += 1
                async with results_lock:
                    results["total_failed"] = results.get("total_failed", 0) + 1

            # 延迟 + 随机抖动 (±30%)
            base_delay = random.uniform(delay_min, delay_max)
            jitter = base_delay * random.uniform(-0.3, 0.3)
            delay = max(10, base_delay + jitter)
            await _log(db, task_id, account_id, "DEBUG", f"等待 {delay:.0f} 秒...")
            # 分段 sleep, 每 60 秒刷新一次 last_used 防止被空闲清理断开
            remaining = delay
            while remaining > 0:
                chunk = min(remaining, 60)
                await asyncio.sleep(chunk)
                touch_client(account_id)
                remaining -= chunk

        async with results_lock:
            results["account_results"][account_id] = {
                "sent": sent_count,
                "failed": failed_count,
            }

        await _log(db, task_id, account_id, "INFO",
                   f"群聊发送任务完成: 成功 {sent_count}, 失败 {failed_count}")
