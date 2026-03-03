"""Telegram 多账号客户端管理器 — 带 LRU 池化 + 空闲超时

改进 (100 账号版):
- LRU 池: 最多保持 CLIENT_POOL_MAX 个活跃连接
- 空闲超时: 超过 CLIENT_IDLE_TIMEOUT 秒无操作自动断开
- 按需连接: 操作时才从 session_string 恢复
- 异步锁: 避免并发创建同一账号的客户端
"""
import asyncio
import logging
import random
import time
from typing import Optional
from collections import OrderedDict

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    AuthKeyError,
)

from app.config import DEVICE_FINGERPRINTS, settings
from app.services.proxy_manager import get_proxy_socks_port

logger = logging.getLogger(__name__)


class ClientPool:
    """LRU 客户端池, 支持空闲超时和最大连接数限制"""

    def __init__(self, max_size: int, idle_timeout: int):
        self._clients: OrderedDict[str, TelegramClient] = OrderedDict()
        self._last_used: dict[str, float] = {}  # account_id -> timestamp
        self._max_size = max_size
        self._idle_timeout = idle_timeout
        self._lock = asyncio.Lock()

    def get(self, account_id: str) -> Optional[TelegramClient]:
        """获取客户端 (不创建), 更新 LRU"""
        if account_id in self._clients:
            self._clients.move_to_end(account_id)
            self._last_used[account_id] = time.time()
            return self._clients[account_id]
        return None

    async def put(self, account_id: str, client: TelegramClient):
        """放入/更新客户端, 若超出容量则逐出最久未用的"""
        async with self._lock:
            if account_id in self._clients:
                self._clients.move_to_end(account_id)
            else:
                # 检查容量
                while len(self._clients) >= self._max_size:
                    evict_id, evict_client = self._clients.popitem(last=False)
                    self._last_used.pop(evict_id, None)
                    logger.debug(f"LRU 逐出客户端: {evict_id}")
                    try:
                        await evict_client.disconnect()
                    except Exception:
                        pass
                self._clients[account_id] = client
            self._last_used[account_id] = time.time()

    async def remove(self, account_id: str):
        """移除并断开客户端"""
        async with self._lock:
            client = self._clients.pop(account_id, None)
            self._last_used.pop(account_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def disconnect_all(self):
        """断开所有客户端"""
        async with self._lock:
            for account_id, client in list(self._clients.items()):
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self._clients.clear()
            self._last_used.clear()

    async def cleanup_idle(self):
        """清理空闲超时的客户端"""
        now = time.time()
        to_remove = []
        for account_id, last in list(self._last_used.items()):
            if now - last > self._idle_timeout:
                to_remove.append(account_id)

        for account_id in to_remove:
            logger.debug(f"空闲超时断开: {account_id}")
            await self.remove(account_id)

    def size(self) -> int:
        return len(self._clients)

    def contains(self, account_id: str) -> bool:
        return account_id in self._clients


# 全局客户端池
_pool = ClientPool(max_size=settings.CLIENT_POOL_MAX, idle_timeout=settings.CLIENT_IDLE_TIMEOUT)

# 创建锁: 防止并发为同一 account_id 创建客户端
_create_locks: dict[str, asyncio.Lock] = {}


def _get_create_lock(account_id: str) -> asyncio.Lock:
    if account_id not in _create_locks:
        _create_locks[account_id] = asyncio.Lock()
    return _create_locks[account_id]


def _build_proxy_tuple(proxy_id: str | None) -> tuple | None:
    """获取代理的 SOCKS5 元组 (用于 Telethon)"""
    if not proxy_id:
        return None
    port = get_proxy_socks_port(proxy_id)
    if port:
        import socks
        return (socks.SOCKS5, "127.0.0.1", port)
    return None


def _get_random_fingerprint() -> dict:
    """从指纹池中随机选取一组设备参数"""
    return random.choice(DEVICE_FINGERPRINTS)


async def create_client(
    api_id: int,
    api_hash: str,
    session_string: str = "",
    device_model: str = "",
    system_version: str = "",
    app_version: str = "",
    lang_code: str = "en",
    system_lang_code: str = "en-US",
    proxy_id: str | None = None,
) -> TelegramClient:
    """创建 Telethon 客户端实例"""
    session = StringSession(session_string) if session_string else StringSession()
    proxy = _build_proxy_tuple(proxy_id)

    client = TelegramClient(
        session,
        api_id,
        api_hash,
        device_model=device_model or "Unknown",
        system_version=system_version or "Unknown",
        app_version=app_version or "1.0.0",
        lang_code=lang_code,
        system_lang_code=system_lang_code,
        proxy=proxy,
    )
    return client


async def get_or_create_client(
    account_id: str,
    api_id: int,
    api_hash: str,
    session_string: str = "",
    device_model: str = "",
    system_version: str = "",
    app_version: str = "",
    lang_code: str = "en",
    system_lang_code: str = "en-US",
    proxy_id: str | None = None,
) -> TelegramClient:
    """获取或创建客户端, 有 LRU 池缓存"""
    # 先尝试从池中获取
    client = _pool.get(account_id)
    if client and client.is_connected():
        return client

    # 加锁防止并发创建
    lock = _get_create_lock(account_id)
    async with lock:
        # 双重检查
        client = _pool.get(account_id)
        if client and client.is_connected():
            return client

        # 已有但断开的 → 尝试重连
        if client:
            try:
                await client.connect()
                if await client.is_user_authorized():
                    return client
            except Exception as e:
                logger.warning(f"重连失败 (account={account_id}): {e}")
                await _pool.remove(account_id)

        # 创建新客户端
        client = await create_client(
            api_id, api_hash, session_string,
            device_model, system_version, app_version,
            lang_code, system_lang_code, proxy_id,
        )
        await client.connect()
        await _pool.put(account_id, client)
        return client


async def send_code(
    account_id: str,
    phone: str,
    api_id: int,
    api_hash: str,
    device_model: str = "",
    system_version: str = "",
    app_version: str = "",
    lang_code: str = "en",
    system_lang_code: str = "en-US",
    proxy_id: str | None = None,
) -> str:
    """发送验证码, 返回 phone_code_hash"""
    client = await get_or_create_client(
        account_id, api_id, api_hash, "",
        device_model, system_version, app_version,
        lang_code, system_lang_code, proxy_id,
    )
    result = await client.send_code_request(phone)
    logger.info(f"验证码已发送: phone={phone}, account={account_id}")
    return result.phone_code_hash


async def sign_in_with_code(
    account_id: str,
    phone: str,
    code: str,
    phone_code_hash: str,
) -> dict:
    """
    用验证码登录
    返回:
        {"status": "ok", "session_string": "..."}
        {"status": "2fa_required"}
        {"status": "error", "message": "..."}
    """
    client = _pool.get(account_id)
    if not client:
        return {"status": "error", "message": "客户端不存在，请重新发送验证码"}

    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        session_str = client.session.save()
        logger.info(f"验证码登录成功: phone={phone}")
        return {"status": "ok", "session_string": session_str}
    except SessionPasswordNeededError:
        logger.info(f"需要2FA验证: phone={phone}")
        return {"status": "2fa_required"}
    except PhoneCodeInvalidError:
        return {"status": "error", "message": "验证码无效"}
    except PhoneCodeExpiredError:
        return {"status": "error", "message": "验证码已过期"}
    except FloodWaitError as e:
        return {"status": "error", "message": f"操作过于频繁，请等待 {e.seconds} 秒"}
    except Exception as e:
        logger.error(f"登录失败: {e}")
        return {"status": "error", "message": str(e)}


async def sign_in_with_2fa(account_id: str, password: str) -> dict:
    """2FA 密码验证"""
    client = _pool.get(account_id)
    if not client:
        return {"status": "error", "message": "客户端不存在"}

    try:
        await client.sign_in(password=password)
        session_str = client.session.save()
        logger.info(f"2FA 登录成功: account={account_id}")
        return {"status": "ok", "session_string": session_str}
    except Exception as e:
        logger.error(f"2FA 登录失败: {e}")
        return {"status": "error", "message": str(e)}


async def restore_session(
    account_id: str,
    api_id: int,
    api_hash: str,
    session_string: str,
    device_model: str = "",
    system_version: str = "",
    app_version: str = "",
    lang_code: str = "en",
    system_lang_code: str = "en-US",
    proxy_id: str | None = None,
) -> bool:
    """从 StringSession 恢复登录"""
    try:
        client = await get_or_create_client(
            account_id, api_id, api_hash, session_string,
            device_model, system_version, app_version,
            lang_code, system_lang_code, proxy_id,
        )
        if await client.is_user_authorized():
            logger.info(f"Session 恢复成功: account={account_id}")
            return True
        else:
            logger.warning(f"Session 无效: account={account_id}")
            return False
    except AuthKeyError:
        logger.error(f"Session 已失效 (AuthKeyError): account={account_id}")
        return False
    except Exception as e:
        logger.error(f"Session 恢复失败: account={account_id}, error={e}")
        return False


def get_client(account_id: str) -> TelegramClient | None:
    """获取已登录的客户端"""
    return _pool.get(account_id)


async def disconnect_client(account_id: str):
    """断开客户端连接"""
    await _pool.remove(account_id)


async def disconnect_all():
    """断开所有客户端"""
    await _pool.disconnect_all()


def get_connected_count() -> int:
    """获取已连接的客户端数量"""
    return _pool.size()


async def cleanup_idle_clients():
    """清理空闲超时的客户端（由定时器调用）"""
    await _pool.cleanup_idle()
