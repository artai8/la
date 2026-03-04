"""SQLAlchemy ORM 模型"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, Text, DateTime,
    ForeignKey, JSON, Float, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _utcnow():
    """返回当前 UTC 时间（naive，兼容 TIMESTAMP WITHOUT TIME ZONE 列）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_uuid():
    return str(uuid.uuid4())


class Setting(Base):
    """全局设置（Supabase URL/Key 等）"""
    __tablename__ = "settings"

    key = Column(String(255), primary_key=True)
    value = Column(Text, default="")
    encrypted = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class TelegramApiConfig(Base):
    """Telegram API 配置池"""
    __tablename__ = "telegram_api_configs"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    api_id = Column(Integer, nullable=False)
    api_hash = Column(String(64), nullable=False)
    label = Column(String(128), default="")
    created_at = Column(DateTime, default=_utcnow)

    accounts = relationship("Account", back_populates="api_config")


class Proxy(Base):
    """代理节点"""
    __tablename__ = "proxies"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    protocol = Column(String(16), nullable=False)  # vless / vmess / trojan
    raw_link = Column(Text, nullable=False)
    address = Column(String(255), default="")
    port = Column(Integer, default=0)
    config_json = Column(JSON, default=dict)
    local_port = Column(Integer, nullable=True, unique=True)
    status = Column(String(16), default="unchecked")  # active / dead / unchecked
    subscription_url = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)

    accounts = relationship("Account", back_populates="proxy")


class Account(Base):
    """Telegram 账号"""
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    phone = Column(String(32), nullable=False, unique=True)
    api_config_id = Column(String(36), ForeignKey("telegram_api_configs.id"), nullable=True)
    proxy_id = Column(String(36), ForeignKey("proxies.id"), nullable=True)
    session_string = Column(Text, default="")
    device_model = Column(String(128), default="")
    system_version = Column(String(64), default="")
    app_version = Column(String(32), default="")
    lang_code = Column(String(8), default="en")
    system_lang_code = Column(String(8), default="en-US")
    nickname = Column(String(128), default="")  # 用户备注
    status = Column(String(32), default="inactive")  # active / inactive / banned / limited / 2fa_required
    two_fa_password = Column(Text, default="")
    is_restricted = Column(Boolean, default=False)
    phone_code_hash = Column(String(128), default="")  # 登录流程中暂存

    # ── 100 账号扩展: 限流 / 冷却 / 健康 ──
    daily_invite_count = Column(Integer, default=0)          # 当日已邀请人数
    daily_invite_reset_at = Column(DateTime, nullable=True)  # 计数重置时间
    daily_message_count = Column(Integer, default=0)         # 当日已发消息数
    daily_message_reset_at = Column(DateTime, nullable=True)
    cooldown_until = Column(DateTime, nullable=True)         # 冷却截止时间
    last_used_at = Column(DateTime, nullable=True)           # 上次操作时间
    health_score = Column(Float, default=100.0)              # 健康评分 0‑100
    flood_wait_count = Column(Integer, default=0)            # 累计 FloodWait 次数
    peer_flood_count = Column(Integer, default=0)            # 累计 PeerFlood 次数
    fingerprint_hash = Column(String(64), default="")        # 绑定的设备指纹 hash
    restriction_details = Column(JSON, default=dict)         # 上次限制检测详情
    restriction_checked_at = Column(DateTime, nullable=True) # 上次限制检测时间
    is_new = Column(Boolean, default=True)                   # 新号标记 (7天内不执行任务)
    registered_at = Column(DateTime, nullable=True)          # 首次登录成功时间

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    api_config = relationship("TelegramApiConfig", back_populates="accounts")
    proxy = relationship("Proxy", back_populates="accounts")
    task_logs = relationship("TaskLog", back_populates="account")


class Group(Base):
    """群组"""
    __tablename__ = "groups"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    telegram_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String(128), default="")
    title = Column(String(256), default="")
    member_count = Column(Integer, default=0)
    group_type = Column(String(32), default="supergroup")  # group / supergroup / channel
    last_scraped_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    scraped_members = relationship("ScrapedMember", back_populates="group")
    scraped_messages = relationship("ScrapedMessage", back_populates="group")


class ScrapedMember(Base):
    """采集的群成员"""
    __tablename__ = "scraped_members"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(BigInteger, nullable=False)
    access_hash = Column(BigInteger, nullable=True)  # Telegram access_hash, 用于无 username 用户的 InputPeerUser
    username = Column(String(128), default="")
    first_name = Column(String(128), default="")
    last_name = Column(String(128), default="")
    phone = Column(String(32), default="")
    group_id = Column(String(36), ForeignKey("groups.id"), nullable=False)
    last_online = Column(DateTime, nullable=True)
    online_status = Column(String(32), default="")  # recently / last_week / last_month / long_ago / unknown
    is_admin = Column(Boolean, default=False)
    is_bot = Column(Boolean, default=False)
    is_invited = Column(Boolean, default=False)
    invite_status = Column(String(16), default="pending")  # pending / success / failed
    scraped_by = Column(String(36), ForeignKey("accounts.id"), nullable=True)
    scraped_at = Column(DateTime, default=_utcnow)

    group = relationship("Group", back_populates="scraped_members")

    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_member_group"),
        Index("ix_scraped_members_invited", "is_invited", "invite_status"),
    )


class ScrapedMessage(Base):
    """采集的聊天内容"""
    __tablename__ = "scraped_messages"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    telegram_msg_id = Column(BigInteger, nullable=False)
    group_id = Column(String(36), ForeignKey("groups.id"), nullable=False)
    sender_id = Column(BigInteger, nullable=True)
    sender_username = Column(String(128), default="")
    text = Column(Text, default="")
    date = Column(DateTime, nullable=True)
    is_sent = Column(Boolean, default=False)
    scraped_by = Column(String(36), ForeignKey("accounts.id"), nullable=True)
    scraped_at = Column(DateTime, default=_utcnow)

    group = relationship("Group", back_populates="scraped_messages")

    __table_args__ = (
        UniqueConstraint("telegram_msg_id", "group_id", name="uq_msg_group"),
        Index("ix_scraped_messages_sent", "is_sent"),
    )


class Task(Base):
    """定时任务"""
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(256), default="")
    task_type = Column(String(32), nullable=False)  # scrape_members / scrape_messages / invite / chat / nurture / check_restriction
    config_json = Column(JSON, default=dict)
    status = Column(String(16), default="idle")  # idle / running / paused / error / cancelled
    cron_expression = Column(String(64), default="0 8 * * *")  # 默认每天 8 点
    enabled = Column(Boolean, default=True)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    account_ids = Column(JSON, default=list)  # 执行任务的账号 ID 列表

    # ── 100 账号扩展: 进度 / 重试 / 并发 ──
    max_concurrency = Column(Integer, default=3)             # 任务级最大并发
    progress_json = Column(JSON, default=dict)               # 进度追踪 {account_id: {done:n, fail:n}}
    retry_count = Column(Integer, default=0)                 # 已重试次数
    max_retries = Column(Integer, default=3)                 # 最大重试次数
    is_cancelled = Column(Boolean, default=False)            # 中途取消标记

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    logs = relationship("TaskLog", back_populates="task")


class TaskLog(Base):
    """任务日志"""
    __tablename__ = "task_logs"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=True)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=True)
    module = Column(String(64), default="system")  # system / scrape / invite / chat / account / proxy
    level = Column(String(16), default="INFO")  # DEBUG / INFO / WARNING / ERROR
    message = Column(Text, default="")
    timestamp = Column(DateTime, default=_utcnow)

    task = relationship("Task", back_populates="logs")
    account = relationship("Account", back_populates="task_logs")

    __table_args__ = (
        Index("ix_task_logs_task", "task_id"),
        Index("ix_task_logs_ts", "timestamp"),
    )


class KeywordBlacklist(Base):
    """关键词黑名单"""
    __tablename__ = "keyword_blacklist"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    keyword = Column(String(256), nullable=False, unique=True)
    created_at = Column(DateTime, default=_utcnow)
