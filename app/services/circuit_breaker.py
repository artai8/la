"""全局熔断器 — 当短时间内多个账号被 PeerFlood 时暂停全部操作

机制:
- 在 CIRCUIT_BREAKER_WINDOW 秒内，若 ≥ CIRCUIT_BREAKER_THRESHOLD 个不同账号
  触发 PeerFlood，则进入 OPEN 状态
- OPEN 状态下所有邀请/群发操作将被拒绝
- 经过 CIRCUIT_BREAKER_COOLDOWN 秒后自动恢复
"""
import asyncio
import logging
import time
from collections import deque
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self):
        self._events: deque[tuple[float, str]] = deque()  # (timestamp, account_id)
        self._open_until: float = 0.0
        self._lock = asyncio.Lock()

    def is_open(self) -> bool:
        """熔断器是否处于打开（拒绝）状态"""
        return time.time() < self._open_until

    async def record_peer_flood(self, account_id: str):
        """记录一个 PeerFlood 事件，检查是否需要触发熔断"""
        async with self._lock:
            now = time.time()
            self._events.append((now, account_id))

            # 清理过期事件
            cutoff = now - settings.CIRCUIT_BREAKER_WINDOW
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()

            # 统计窗口内不同账号数
            unique_accounts = {aid for _, aid in self._events}
            if len(unique_accounts) >= settings.CIRCUIT_BREAKER_THRESHOLD:
                self._open_until = now + settings.CIRCUIT_BREAKER_COOLDOWN
                logger.critical(
                    f"🔴 熔断器触发! {len(unique_accounts)} 个账号在 "
                    f"{settings.CIRCUIT_BREAKER_WINDOW}s 内被 PeerFlood，"
                    f"暂停操作 {settings.CIRCUIT_BREAKER_COOLDOWN}s"
                )
                self._events.clear()

    def remaining_seconds(self) -> float:
        """返回距离熔断器关闭的剩余秒数; ≤0 表示已关闭"""
        return max(0, self._open_until - time.time())

    def reset(self):
        """手动重置熔断器"""
        self._open_until = 0.0
        self._events.clear()
        logger.info("熔断器已手动重置")


# 全局单例
circuit_breaker = CircuitBreaker()
