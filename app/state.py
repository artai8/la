import asyncio
import random
from typing import Optional
from dataclasses import dataclass, field

@dataclass
class AppState:
    """集中管理所有状态"""
    status: bool = False
    extract: bool = False
    members_ext: list = field(default_factory=list)
    members: list = field(default_factory=list)
    ok_count: int = 0
    bad_count: int = 0
    runs: list = field(default_factory=list)
    final: list = field(default_factory=list)
    max_concurrent: int = 5
    current_task_id: Optional[int] = None
    current_task_type: Optional[str] = None
    extract_running: int = 0
    chat_active: bool = False
    keepalive: bool = False
    auto_online: bool = False
    auto_warmup: bool = False

    def __post_init__(self):
        self._lock = asyncio.Lock()

    async def pop_member(self) -> Optional[str]:
        async with self._lock:
            if self.members:
                member = random.choice(self.members)
                self.members.remove(member)
                return member
            return None

    async def return_member(self, member: str):
        async with self._lock:
            if member not in self.members:
                self.members.append(member)

    def reset_adder(self):
        self.ok_count = 0
        self.bad_count = 0
        self.runs = []
        self.final = []

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "extract": self.extract,
            "members_count": len(self.members),
            "members_ext_count": len(self.members_ext),
            "ok_count": self.ok_count,
            "bad_count": self.bad_count,
            "runs": list(self.runs),
            "final": list(self.final),
            "max_concurrent": self.max_concurrent,
            "current_task_id": self.current_task_id,
            "current_task_type": self.current_task_type,
            "extract_running": self.extract_running,
            "chat_active": self.chat_active,
            "keepalive": self.keepalive,
            "auto_online": self.auto_online,
            "auto_warmup": self.auto_warmup,
        }

state = AppState()
