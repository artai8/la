from typing import Optional
from pydantic import BaseModel

class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    phone: str
    code: str

class PasswordRequest(BaseModel):
    phone: str
    password: str

class ExtractRequest(BaseModel):
    links: list[str]
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    auto_load: bool = False
    exclude_admin: bool = False
    exclude_bot: bool = True
    use_remote_db: bool = True

class ExtractBatchRequest(BaseModel):
    links: list[str]
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    auto_load: bool = False
    exclude_admin: bool = False
    exclude_bot: bool = True
    use_remote_db: bool = True

class ScrapeRequest(BaseModel):
    link: str
    limit: int = 100
    min_length: int = 5
    keywords_blacklist: list[str] = []
    save_to_remote: bool = True

class AdderRequest(BaseModel):
    link: str = ""
    links: list[str] = []
    number_add: int
    number_account: int
    use_remote_db: bool = False
    group_name: str = ""
    group_names: list[str] = []

class NameRequest(BaseModel):
    name: str

class LoginRequest(BaseModel):
    username: str
    password: str

class BootstrapRequest(BaseModel):
    username: str
    password: str

class ApiCredentialRequest(BaseModel):
    api_id: int
    api_hash: str

class ApiUpdateRequest(BaseModel):
    id: int
    api_id: int
    api_hash: str

class ApiToggleRequest(BaseModel):
    id: int
    enabled: bool

class ApiImportRequest(BaseModel):
    lines: str

class ProxyRequest(BaseModel):
    scheme: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    raw_url: str = ""

class ProxyUpdateRequest(BaseModel):
    id: int
    scheme: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    raw_url: str = ""

class ProxyToggleRequest(BaseModel):
    id: int
    enabled: bool = False

class ProxyImportRequest(BaseModel):
    lines: str

class ListValueRequest(BaseModel):
    list_type: str
    value: str

class SettingsRequest(BaseModel):
    key: str
    value: str

class TaskCreateRequest(BaseModel):
    type: str
    payload: dict
    run_at: Optional[int] = None

class TaskIdRequest(BaseModel):
    id: int

class TaskLogRequest(BaseModel):
    id: int

class WorkerPingRequest(BaseModel):
    name: str
    status: str

class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str

class JoinRequest(BaseModel):
    links: list[str]
    number_account: int = 0
    batch_size: int = 0
    account_delay: int = 1

class InviteRequest(BaseModel):
    link: str
    group_names: list[str] = []
    number_add: int
    number_account: int
    use_loaded: bool = True
    use_remote_db: bool = False

class ChatRequest(BaseModel):
    link: str = ""
    links: list[str] = []
    messages: list[str] = []
    number_account: int = 1
    min_delay: int = 10
    max_delay: int = 30
    max_messages: int = 50
    use_remote_db: bool = False

class DMRequest(BaseModel):
    group_name: str
    messages: list[str]
    number_account: int = 1
    min_delay: int = 10
    max_delay: int = 30
    use_loaded: bool = True

class ProfileEditRequest(BaseModel):
    phones: list[str] = []
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    about: Optional[str] = None
    username: Optional[str] = None

class SessionImportRequest(BaseModel):
    api_id: int
    api_hash: str
    session_string: str

class SessionBatchImportRequest(BaseModel):
    lines: str

class AccountKeepaliveRequest(BaseModel):
    phones: list[str] = []

class AccountWarmupRequest(BaseModel):
    phones: list[str] = []
    duration_min: int = 10
    actions: list[str] = ["scroll", "read"]

class AccountSpamCheckRequest(BaseModel):
    phones: list[str] = []

class WarmupRequest(BaseModel):
    number_account: int = 1
    duration_min: int = 10
    actions: list[str] = ["scroll", "read"]

class GroupAssignRequest(BaseModel):
    phones: list[str]
    group_name: str


def _normalize_links(raw: list[str]) -> list[str]:
    items = []
    for line in raw:
        if not line:
            continue
        for part in str(line).splitlines():
            p = part.strip()
            if p:
                items.append(p)
    uniq = []
    seen = set()
    for it in items:
        if it not in seen:
            uniq.append(it)
            seen.add(it)
    return uniq

def _normalize_keywords(raw: list[str]) -> list[str]:
    out = []
    for r in raw:
        if not r:
            continue
        for part in str(r).split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out
