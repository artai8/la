"""Pydantic 请求/响应模型"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ---- 设置 ----
class SupabaseConfig(BaseModel):
    supabase_url: str = ""
    supabase_key: str = ""


class TelegramApiConfigCreate(BaseModel):
    api_id: int
    api_hash: str
    label: str = ""


class TelegramApiConfigBatch(BaseModel):
    """批量添加格式: api_id:api_hash:label 每行一条"""
    raw_text: str


class ProxyCreate(BaseModel):
    raw_link: str


class ProxyBatch(BaseModel):
    raw_text: str  # 每行一条链接


class SubscriptionImport(BaseModel):
    subscription_url: str


# ---- 账号 ----
class AccountLogin(BaseModel):
    phone: str
    api_config_id: str
    proxy_id: Optional[str] = None


class VerifyCode(BaseModel):
    account_id: str
    code: str


class Verify2FA(BaseModel):
    account_id: str
    password: str


class AccountNickname(BaseModel):
    account_id: str
    nickname: str


# ---- 采集 ----
class ScrapeRequest(BaseModel):
    group_inputs: str  # 每行一个群标识
    account_ids: list[str] = []
    filter_admins: bool = True
    filter_bots: bool = True
    online_filter: str = "none"  # none / 1d / 3d / 7d / 30d
    save_local: bool = True
    save_remote: bool = False


class ScrapeMessagesRequest(BaseModel):
    group_inputs: str
    account_ids: list[str] = []
    filter_admins: bool = True
    filter_bots: bool = True
    save_local: bool = True
    save_remote: bool = False
    message_limit: int = 100


# ---- 拉人 / 群聊 ----
class InviteRequest(BaseModel):
    source_group_ids: list[str] = []  # 来源群 DB id
    target_groups: str  # 目标群标识, 每行一个
    account_ids: list[str] = []
    delay_min: int = 300
    delay_max: int = 600
    concurrency: int = 1
    per_account_limit: int = 5
    use_remote_db: bool = False


class ChatSendRequest(BaseModel):
    source_group_ids: list[str] = []
    target_groups: str
    account_ids: list[str] = []
    delay_min: int = 300
    delay_max: int = 600
    concurrency: int = 5
    per_account_limit: int = 10


# ---- 任务 ----
class TaskCreate(BaseModel):
    name: str = ""
    task_type: str  # scrape_members / scrape_messages / invite / chat
    config_json: dict = {}
    cron_expression: str = "0 8 * * *"
    account_ids: list[str] = []


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    config_json: Optional[dict] = None
    cron_expression: Optional[str] = None
    account_ids: Optional[list[str]] = None
    enabled: Optional[bool] = None


# ---- 关键词 ----
class KeywordCreate(BaseModel):
    keyword: str


class KeywordBatch(BaseModel):
    raw_text: str  # 每行一个关键词
