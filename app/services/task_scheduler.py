"""APScheduler 定时任务调度"""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskLog
from app.database import async_session_factory

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(
    jobstores={"default": MemoryJobStore()},
    job_defaults={"coalesce": True, "max_instances": 1},
)


async def _check_cancelled(task_id: str, db: AsyncSession) -> bool:
    """检查任务是否已取消"""
    result = await db.execute(select(Task.is_cancelled).where(Task.id == task_id))
    cancelled = result.scalar()
    return bool(cancelled)


async def _update_progress(task_id: str, progress: dict, db: AsyncSession):
    """更新任务进度"""
    await db.execute(
        update(Task).where(Task.id == task_id).values(progress_json=progress)
    )
    await db.commit()


async def _execute_task(task_id: str):
    """执行定时任务"""
    async with async_session_factory() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            logger.error(f"任务不存在: {task_id}")
            return

        if not task.enabled:
            logger.info(f"任务已禁用: {task.name} ({task_id})")
            return

        # 检查是否已取消
        if task.is_cancelled:
            logger.info(f"任务已取消: {task.name} ({task_id})")
            task.status = "cancelled"
            await db.commit()
            return

        task.status = "running"
        task.last_run = datetime.utcnow()
        task.progress_json = {}
        await db.commit()

        log_entry = TaskLog(
            task_id=task_id,
            module="scheduler",
            level="INFO",
            message=f"定时任务开始执行: {task.name} (类型: {task.task_type})",
        )
        db.add(log_entry)
        await db.commit()

        try:
            config = task.config_json or {}
            account_ids = task.account_ids or []

            if task.task_type == "scrape_members":
                from app.services.scrape_service import scrape_group_members
                group_inputs = config.get("group_inputs", "").strip().splitlines()
                for i, aid in enumerate(account_ids):
                    if await _check_cancelled(task_id, db):
                        logger.info(f"任务被中途取消: {task.name}")
                        break
                    await scrape_group_members(
                        account_id=aid,
                        group_identifiers=group_inputs,
                        db=db,
                        filter_admins=config.get("filter_admins", True),
                        filter_bots=config.get("filter_bots", True),
                        online_filter=config.get("online_filter", "none"),
                        save_local=config.get("save_local", True),
                        save_remote=config.get("save_remote", False),
                        task_id=task_id,
                    )
                    await _update_progress(task_id, {"completed_accounts": i + 1, "total_accounts": len(account_ids)}, db)

            elif task.task_type == "scrape_messages":
                from app.services.scrape_service import scrape_group_messages
                group_inputs = config.get("group_inputs", "").strip().splitlines()
                for i, aid in enumerate(account_ids):
                    if await _check_cancelled(task_id, db):
                        break
                    await scrape_group_messages(
                        account_id=aid,
                        group_identifiers=group_inputs,
                        db=db,
                        filter_admins=config.get("filter_admins", True),
                        filter_bots=config.get("filter_bots", True),
                        save_local=config.get("save_local", True),
                        save_remote=config.get("save_remote", False),
                        message_limit=config.get("message_limit", 100),
                        task_id=task_id,
                    )
                    await _update_progress(task_id, {"completed_accounts": i + 1, "total_accounts": len(account_ids)}, db)

            elif task.task_type == "invite":
                from app.services.invite_service import invite_members
                target_groups = config.get("target_groups", "").strip().splitlines()
                source_group_ids = config.get("source_group_ids", [])
                await invite_members(
                    account_ids=account_ids,
                    source_group_ids=source_group_ids,
                    target_group_inputs=target_groups,
                    db=db,
                    delay_min=config.get("delay_min", 300),
                    delay_max=config.get("delay_max", 600),
                    concurrency=config.get("concurrency", 1),
                    per_account_limit=config.get("per_account_limit", 5),
                    use_remote_db=config.get("use_remote_db", False),
                    task_id=task_id,
                )

            elif task.task_type == "chat":
                from app.services.chat_service import send_messages
                target_groups = config.get("target_groups", "").strip().splitlines()
                source_group_ids = config.get("source_group_ids", [])
                await send_messages(
                    account_ids=account_ids,
                    source_group_ids=source_group_ids,
                    target_group_inputs=target_groups,
                    db=db,
                    delay_min=config.get("delay_min", 300),
                    delay_max=config.get("delay_max", 600),
                    concurrency=config.get("concurrency", 5),
                    per_account_limit=config.get("per_account_limit", 10),
                    task_id=task_id,
                )

            elif task.task_type == "nurture":
                from app.services.account_service import batch_nurture
                concurrency = config.get("concurrency", 3)
                await batch_nurture(
                    account_ids=account_ids,
                    concurrency=concurrency,
                )

            elif task.task_type == "check_restriction":
                from app.services.account_service import check_restriction
                from app.models import Account
                progress = {}
                for i, aid in enumerate(account_ids):
                    if await _check_cancelled(task_id, db):
                        break
                    try:
                        restriction_result = await check_restriction(aid)
                        acc_result = await db.execute(select(Account).where(Account.id == aid))
                        account = acc_result.scalar_one_or_none()
                        if account and restriction_result.get("restricted") is not None:
                            account.is_restricted = restriction_result["restricted"]
                            account.restriction_details = restriction_result.get("details", {})
                            account.restriction_checked_at = datetime.utcnow()
                        progress[aid] = {"restricted": restriction_result.get("restricted"), "done": True}
                    except Exception as e:
                        progress[aid] = {"error": str(e), "done": True}
                        logger.error(f"检测账号 {aid} 限制失败: {e}")
                    await _update_progress(task_id, progress, db)
                    # 间隔避免频率限制
                    if i < len(account_ids) - 1:
                        await asyncio.sleep(5)

            # 检查最终取消状态
            final_result = await db.execute(select(Task).where(Task.id == task_id))
            final_task = final_result.scalar_one_or_none()
            if final_task and final_task.is_cancelled:
                final_task.status = "cancelled"
                cancel_log = TaskLog(task_id=task_id, module="scheduler", level="WARNING",
                                     message=f"任务已被取消: {task.name}")
                db.add(cancel_log)
            else:
                if final_task:
                    final_task.status = "idle"
                log_entry2 = TaskLog(task_id=task_id, module="scheduler", level="INFO",
                                     message=f"定时任务执行完成: {task.name}")
                db.add(log_entry2)
            await db.commit()

        except Exception as e:
            logger.error(f"定时任务执行失败: {task.name}: {e}")
            # 重试逻辑
            retry_result = await db.execute(select(Task).where(Task.id == task_id))
            retry_task = retry_result.scalar_one_or_none()
            if retry_task and retry_task.retry_count < retry_task.max_retries:
                retry_task.retry_count += 1
                retry_task.status = "idle"
                retry_log = TaskLog(
                    task_id=task_id, module="scheduler", level="WARNING",
                    message=f"任务失败, 将重试 ({retry_task.retry_count}/{retry_task.max_retries}): {e}",
                )
                db.add(retry_log)
                await db.commit()
                logger.info(f"任务 {task.name} 将重试 ({retry_task.retry_count}/{retry_task.max_retries})")
            else:
                if retry_task:
                    retry_task.status = "error"
                error_log = TaskLog(
                    task_id=task_id, module="scheduler", level="ERROR",
                    message=f"定时任务执行错误 (已达最大重试): {e}",
                )
                db.add(error_log)
                await db.commit()


def register_task(task_id: str, cron_expression: str):
    """注册定时任务到 APScheduler"""
    try:
        parts = cron_expression.split()
        if len(parts) == 5:
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )
        else:
            trigger = CronTrigger(hour=8, minute=0)  # 默认每天 8 点

        scheduler.add_job(
            _execute_task,
            trigger=trigger,
            id=task_id,
            args=[task_id],
            replace_existing=True,
        )
        logger.info(f"定时任务已注册: {task_id} cron={cron_expression}")
    except Exception as e:
        logger.error(f"注册定时任务失败: {e}")


def unregister_task(task_id: str):
    """取消注册定时任务"""
    try:
        scheduler.remove_job(task_id)
        logger.info(f"定时任务已取消: {task_id}")
    except Exception:
        pass


async def restore_tasks():
    """从数据库恢复已注册的定时任务"""
    async with async_session_factory() as db:
        result = await db.execute(select(Task).where(Task.enabled == True))
        tasks = result.scalars().all()
        for task in tasks:
            register_task(task.id, task.cron_expression)
        logger.info(f"从数据库恢复了 {len(tasks)} 个定时任务")


def start_scheduler():
    """启动调度器"""
    if not scheduler.running:
        # 注册空闲客户端清理任务 (每60秒)
        from app.services.telegram_client import cleanup_idle_clients
        scheduler.add_job(
            cleanup_idle_clients,
            trigger=IntervalTrigger(seconds=60),
            id="__cleanup_idle_clients__",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("APScheduler 调度器已启动 (含空闲客户端清理)")


def shutdown_scheduler():
    """关闭调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler 调度器已关闭")
