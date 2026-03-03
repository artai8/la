"""启动时批量恢复 Telegram Session

从数据库查询所有 status='active' 且有 session_string 的账号，
分批连接以避免同时 100 个连接触发 Telegram 风控。
"""
import asyncio
import logging

from sqlalchemy import select, and_

from app.config import settings
from app.models import Account
from app.database import async_session_factory
from app.services.telegram_client import restore_session
from app.services.proxy_manager import ensure_proxy_running

from app.models import Proxy as ProxyModel

logger = logging.getLogger(__name__)


async def restore_all_sessions() -> tuple[int, int]:
    """
    分批恢复所有活跃账号的客户端连接。
    Returns: (成功数, 失败数)
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(Account).where(
                and_(
                    Account.status == "active",
                    Account.session_string != "",
                    Account.session_string.isnot(None),
                )
            ).order_by(Account.health_score.desc())
        )
        accounts = result.scalars().all()

    if not accounts:
        logger.info("没有需要恢复的活跃账号")
        return (0, 0)

    total = len(accounts)
    logger.info(f"准备恢复 {total} 个账号的 Session (每批 {settings.SESSION_RESTORE_BATCH} 个)")

    restored = 0
    failed = 0

    for i in range(0, total, settings.SESSION_RESTORE_BATCH):
        batch = accounts[i:i + settings.SESSION_RESTORE_BATCH]
        batch_num = i // settings.SESSION_RESTORE_BATCH + 1
        logger.info(f"恢复第 {batch_num} 批 ({len(batch)} 个账号)...")

        tasks = [_restore_one(acc) for acc in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for acc, res in zip(batch, results):
            if isinstance(res, Exception):
                logger.error(f"恢复异常 {acc.phone}: {res}")
                failed += 1
            elif res:
                restored += 1
            else:
                failed += 1

        # 批次间延迟
        if i + settings.SESSION_RESTORE_BATCH < total:
            logger.debug(f"等待 {settings.SESSION_RESTORE_DELAY}s 再恢复下一批...")
            await asyncio.sleep(settings.SESSION_RESTORE_DELAY)

    logger.info(f"Session 恢复完成: {restored}/{total} 成功, {failed} 失败")
    return (restored, failed)


async def _restore_one(account: Account) -> bool:
    """恢复单个账号"""
    try:
        # 确保代理运行
        if account.proxy_id:
            async with async_session_factory() as proxy_db:
                proxy_result = await proxy_db.execute(
                    select(ProxyModel).where(ProxyModel.id == account.proxy_id)
                )
                proxy_obj = proxy_result.scalar_one_or_none()
                if proxy_obj:
                    proxy_data = {
                        "protocol": proxy_obj.protocol,
                        "address": proxy_obj.address,
                        "port": proxy_obj.port,
                        "config_json": proxy_obj.config_json,
                    }
                    ensure_proxy_running(account.proxy_id, proxy_data)

        # 需要 API 配置
        if not account.api_config_id:
            logger.warning(f"账号 {account.phone} 缺少 API 配置, 跳过恢复")
            return False

        # 懒加载 api_config
        async with async_session_factory() as db:
            from app.models import TelegramApiConfig
            result = await db.execute(
                select(TelegramApiConfig).where(TelegramApiConfig.id == account.api_config_id)
            )
            api_cfg = result.scalar_one_or_none()
            if not api_cfg:
                logger.warning(f"账号 {account.phone} 的 API 配置不存在, 跳过恢复")
                return False

            success = await restore_session(
                account_id=account.id,
                api_id=api_cfg.api_id,
                api_hash=api_cfg.api_hash,
                session_string=account.session_string,
                device_model=account.device_model,
                system_version=account.system_version,
                app_version=account.app_version,
                lang_code=account.lang_code,
                system_lang_code=account.system_lang_code,
                proxy_id=account.proxy_id,
            )

            if not success:
                # 标记为 inactive
                account_obj = await db.get(Account, account.id)
                if account_obj:
                    account_obj.status = "inactive"
                    await db.commit()
                    logger.warning(f"账号 {account.phone} Session 无效, 已标记 inactive")

            return success
    except Exception as e:
        logger.error(f"恢复账号 {account.phone} 失败: {e}")
        return False
