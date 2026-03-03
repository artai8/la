"""拉人入群服务 — 支持100账号并发安全 + 防封"""
import asyncio
import logging
import random
from datetime import datetime

from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.errors import (
    FloodWaitError,
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    UserChannelsTooMuchError,
    ChatWriteForbiddenError,
    UserKickedError,
    UserBannedInChannelError,
    PeerFloodError,
    InputUserDeactivatedError,
    UserAlreadyParticipantError,
)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update

from app.models import ScrapedMember, Group, TaskLog
from app.services.telegram_client import get_client
from app.services.scrape_service import resolve_group
from app.services.circuit_breaker import circuit_breaker
from app.services.account_scheduler import record_operation, record_flood_wait, record_peer_flood
from app.database import get_supabase, async_session_factory

logger = logging.getLogger(__name__)


async def _log(db: AsyncSession, task_id: str | None, account_id: str, level: str, message: str):
    """写日志"""
    log_entry = TaskLog(
        task_id=task_id,
        account_id=account_id,
        module="invite",
        level=level,
        message=message,
    )
    db.add(log_entry)
    await db.commit()
    logger.log(getattr(logging, level, logging.INFO), f"[invite] {message}")


async def invite_members(
    account_ids: list[str],
    source_group_ids: list[str],
    target_group_inputs: list[str],
    db: AsyncSession,
    delay_min: int = 300,
    delay_max: int = 600,
    concurrency: int = 1,
    per_account_limit: int = 5,
    use_remote_db: bool = False,
    task_id: str | None = None,
) -> dict:
    """
    拉人入群
    - 多账号并发, 通过 Semaphore 控制
    - 每个账号拉取 per_account_limit 个人
    - 已拉过的人不再拉取 (is_invited=True)
    - 失败的不再重试 (invite_status='failed')
    - 集成 circuit_breaker + account_scheduler
    """
    # 前置: 检查熔断器
    if circuit_breaker.is_open():
        remaining = circuit_breaker.remaining_seconds()
        logger.warning(f"熔断器已开启, 拒绝执行拉人任务, 剩余冷却 {remaining:.0f}s")
        return {
            "total_invited": 0, "total_failed": 0, "total_skipped": 0,
            "errors": [f"熔断器已开启, 请等待 {remaining:.0f} 秒"],
            "account_results": {},
        }

    results = {
        "total_invited": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "errors": [],
        "account_results": {},
    }
    results_lock = asyncio.Lock()

    semaphore = asyncio.Semaphore(concurrency)

    async def _worker(account_id: str):
        async with semaphore:
            await _invite_worker(
                account_id, source_group_ids, target_group_inputs,
                per_account_limit, delay_min, delay_max,
                use_remote_db, task_id, results, results_lock,
            )

    tasks = [asyncio.create_task(_worker(aid)) for aid in account_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

    return results


async def _invite_worker(
    account_id: str,
    source_group_ids: list[str],
    target_group_inputs: list[str],
    per_account_limit: int,
    delay_min: int,
    delay_max: int,
    use_remote_db: bool,
    task_id: str | None,
    results: dict,
    results_lock: asyncio.Lock,
):
    """单个账号的拉人工作 — 线程安全 + 防封"""
    client = get_client(account_id)
    if not client:
        async with results_lock:
            results["errors"].append(f"账号 {account_id} 未连接")
        return

    async with async_session_factory() as db:
        invited_count = 0
        failed_count = 0

        await _log(db, task_id, account_id, "INFO",
                   f"开始拉人任务, 目标拉取 {per_account_limit} 人")

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

        # 从数据库获取未拉取的成员
        query = select(ScrapedMember).where(
            and_(
                ScrapedMember.group_id.in_(source_group_ids),
                ScrapedMember.is_invited == False,
                ScrapedMember.invite_status != "failed",
                ScrapedMember.is_bot == False,
                ScrapedMember.is_admin == False,
            )
        ).limit(per_account_limit)

        result = await db.execute(query)
        members = result.scalars().all()

        if not members:
            await _log(db, task_id, account_id, "INFO", "没有可拉取的成员")
            return

        for member in members:
            # 每次操作前检查熔断器
            if circuit_breaker.is_open():
                await _log(db, task_id, account_id, "WARNING",
                           "熔断器已开启, 停止拉人")
                break

            # 再次检查是否已被其他账号拉过
            await db.refresh(member)
            if member.is_invited:
                async with results_lock:
                    results["total_skipped"] = results.get("total_skipped", 0) + 1
                continue

            try:
                # 获取用户实体
                try:
                    if member.username:
                        user_entity = await client.get_entity(member.username)
                    else:
                        user_entity = await client.get_entity(member.user_id)
                except Exception as e:
                    await _log(db, task_id, account_id, "WARNING",
                               f"无法获取用户 {member.user_id} ({member.username}): {e}")
                    member.invite_status = "failed"
                    await db.commit()
                    failed_count += 1
                    continue

                # 向每个目标群组邀请该用户
                success_any = False
                for target_entity in target_entities:
                    try:
                        await client(InviteToChannelRequest(
                            channel=target_entity,
                            users=[user_entity],
                        ))
                        success_any = True
                        await _log(db, task_id, account_id, "INFO",
                                   f"成功拉取 {member.username or member.user_id} "
                                   f"到 {getattr(target_entity, 'title', '未知')}")
                    except UserAlreadyParticipantError:
                        success_any = True  # 已在群中,算成功
                        await _log(db, task_id, account_id, "INFO",
                                   f"用户 {member.username or member.user_id} 已在群中")
                    except UserPrivacyRestrictedError:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"用户 {member.username or member.user_id} 隐私设置限制")
                        member.invite_status = "failed"
                        break
                    except UserNotMutualContactError:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"用户 {member.username or member.user_id} 非互相联系人")
                        member.invite_status = "failed"
                        break
                    except UserChannelsTooMuchError:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"用户 {member.username or member.user_id} 加入的群过多")
                        member.invite_status = "failed"
                        break
                    except UserKickedError:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"用户 {member.username or member.user_id} 已被踢出")
                        member.invite_status = "failed"
                        break
                    except UserBannedInChannelError:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"用户 {member.username or member.user_id} 已被封禁")
                        member.invite_status = "failed"
                        break
                    except InputUserDeactivatedError:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"用户 {member.username or member.user_id} 账号已注销")
                        member.invite_status = "failed"
                        break
                    except ChatWriteForbiddenError:
                        await _log(db, task_id, account_id, "ERROR",
                                   f"没有权限向群 {getattr(target_entity, 'title', '')} 添加成员")
                        break
                    except PeerFloodError:
                        await _log(db, task_id, account_id, "ERROR",
                                   "PeerFloodError: 账号被限制，停止拉人")
                        member.invite_status = "pending"
                        await db.commit()
                        # 记录到 account_scheduler 和 circuit_breaker
                        await record_peer_flood(account_id)
                        await circuit_breaker.record_peer_flood(account_id)
                        async with results_lock:
                            results["errors"].append(f"账号 {account_id} PeerFloodError")
                            results["account_results"][account_id] = {
                                "invited": invited_count, "failed": failed_count
                            }
                        return
                    except FloodWaitError as e:
                        await _log(db, task_id, account_id, "WARNING",
                                   f"FloodWait: 等待 {e.seconds} 秒")
                        await record_flood_wait(account_id, e.seconds)
                        await asyncio.sleep(e.seconds)

                if success_any:
                    member.is_invited = True
                    member.invite_status = "success"
                    invited_count += 1
                    # 记录到 account_scheduler
                    await record_operation(account_id, "invite")
                    async with results_lock:
                        results["total_invited"] = results.get("total_invited", 0) + 1
                else:
                    if member.invite_status != "failed":
                        member.invite_status = "failed"
                    failed_count += 1
                    async with results_lock:
                        results["total_failed"] = results.get("total_failed", 0) + 1

                await db.commit()

                # 延迟 + 随机抖动 (±30%)
                base_delay = random.uniform(delay_min, delay_max)
                jitter = base_delay * random.uniform(-0.3, 0.3)
                delay = max(10, base_delay + jitter)
                await _log(db, task_id, account_id, "DEBUG", f"等待 {delay:.0f} 秒...")
                await asyncio.sleep(delay)

            except Exception as e:
                msg = f"拉取用户 {member.user_id} 失败: {e}"
                await _log(db, task_id, account_id, "ERROR", msg)
                member.invite_status = "failed"
                await db.commit()
                failed_count += 1
                async with results_lock:
                    results["total_failed"] = results.get("total_failed", 0) + 1

        async with results_lock:
            results["account_results"][account_id] = {
                "invited": invited_count,
                "failed": failed_count,
            }

        await _log(db, task_id, account_id, "INFO",
                   f"拉人任务完成: 成功 {invited_count}, 失败 {failed_count}")
