"""全局配置 - 从环境变量加载"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ---- 数据库 ----
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/la8",
)
# Railway 注入的可能是 postgres:// 开头，需替换为 postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# ---- 应用 ----
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---- Xray ----
XRAY_BIN_PATH = os.getenv("XRAY_BIN_PATH", "/usr/local/bin/xray")
XRAY_CONFIG_DIR = Path(os.getenv("XRAY_CONFIG_DIR", str(BASE_DIR / "xray" / "configs")))
XRAY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
PROXY_BASE_PORT = int(os.getenv("PROXY_BASE_PORT", "10800"))

# ---- Supabase (可在 Web UI 中动态配置) ----
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


# ===================== 100 账号扩展配置 =====================

@dataclass
class AppSettings:
    """可调节的运行时参数"""
    # --- 数据库 ---
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "30"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "70"))

    # --- 客户端池 ---
    CLIENT_POOL_MAX: int = int(os.getenv("CLIENT_POOL_MAX", "30"))       # 最大常驻连接数
    CLIENT_IDLE_TIMEOUT: int = int(os.getenv("CLIENT_IDLE_TIMEOUT", "300"))  # 空闲断开秒数
    SESSION_RESTORE_BATCH: int = 10   # 启动时每批恢复数量
    SESSION_RESTORE_DELAY: float = 3.0  # 每批之间延迟(秒)

    # --- 风控 ---
    DAILY_INVITE_LIMIT: int = int(os.getenv("DAILY_INVITE_LIMIT", "40"))
    DAILY_MESSAGE_LIMIT: int = int(os.getenv("DAILY_MESSAGE_LIMIT", "80"))
    MAX_ACCOUNTS_PER_PROXY: int = int(os.getenv("MAX_ACCOUNTS_PER_PROXY", "3"))
    NEW_ACCOUNT_COOLDOWN_DAYS: int = 7  # 新号养号期天数

    # --- 冷却升级 ---
    COOLDOWN_FLOOD_1: int = 0       # 第1次: 按 Telegram 返回的秒数
    COOLDOWN_FLOOD_2: int = 7200    # 第2次: 2小时
    COOLDOWN_FLOOD_3: int = 86400   # 第3次: 24小时
    COOLDOWN_PEER_FLOOD: int = 86400  # PeerFlood: 24小时

    # --- 全局熔断 ---
    CIRCUIT_BREAKER_THRESHOLD: int = 3     # 10分钟内 N 个账号 PeerFlood 则全局暂停
    CIRCUIT_BREAKER_WINDOW: int = 600      # 熔断检测窗口(秒)
    CIRCUIT_BREAKER_COOLDOWN: int = 1800   # 熔断冷却时间(秒)

    # --- 代理 ---
    XRAY_SINGLE_PROCESS: bool = True  # 单 xray 进程多 inbound 模式

    # --- 路径 ---
    LOG_DIR: str = str(LOG_DIR)


settings = AppSettings()


# ---- 设备指纹池 (120 组, 覆盖 100 账号不重复) ----
DEVICE_FINGERPRINTS = [
    # ── Samsung Galaxy S 系列 ──
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S24+", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S24", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S23 Ultra", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S23+", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S22 Ultra", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S22+", "system_version": "Android 13", "app_version": "10.12.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S22", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S21 Ultra", "system_version": "Android 13", "app_version": "10.11.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S21+", "system_version": "Android 13", "app_version": "10.11.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S21", "system_version": "Android 13", "app_version": "10.11.1", "lang_code": "en", "system_lang_code": "en-US"},
    # ── Samsung Galaxy A / Z / Note ──
    {"device_model": "Samsung Galaxy A54", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy A34", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy A14", "system_version": "Android 13", "app_version": "10.13.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Z Fold5", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Z Fold4", "system_version": "Android 14", "app_version": "10.14.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Z Flip5", "system_version": "Android 13", "app_version": "10.13.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Z Flip4", "system_version": "Android 13", "app_version": "10.13.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Note 20 Ultra", "system_version": "Android 13", "app_version": "10.12.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy Note 20", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "en", "system_lang_code": "en-US"},
    # ── Google Pixel ──
    {"device_model": "Google Pixel 8 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 8", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 8a", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 7 Pro", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 7", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 7a", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 6 Pro", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 6a", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Google Pixel 6", "system_version": "Android 13", "app_version": "10.13.1", "lang_code": "en", "system_lang_code": "en-US"},
    # ── OnePlus ──
    {"device_model": "OnePlus 12", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus 11", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus 10 Pro", "system_version": "Android 14", "app_version": "10.14.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus Nord 3", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus Nord CE 3", "system_version": "Android 13", "app_version": "10.13.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus 9 Pro", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "en", "system_lang_code": "en-US"},
    # ── Xiaomi / Redmi / POCO ──
    {"device_model": "Xiaomi 14 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Xiaomi 14 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Xiaomi 14", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Xiaomi 13 Ultra", "system_version": "Android 13", "app_version": "10.13.1", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Xiaomi 13 Pro", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Xiaomi 13", "system_version": "Android 13", "app_version": "10.13.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi 12", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Xiaomi Redmi Note 13 Pro+", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi Redmi Note 13 Pro", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi Redmi Note 12 Pro", "system_version": "Android 13", "app_version": "10.13.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Xiaomi Redmi K70 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "POCO F6 Pro", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "POCO F5", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "POCO X6 Pro", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    # ── OPPO / Realme ──
    {"device_model": "OPPO Find X7 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "OPPO Find X7", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "OPPO Reno 11 Pro", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "OPPO Reno 11", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OPPO Find N3", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Realme GT 5 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Realme GT Neo 5", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Realme 12 Pro+", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    # ── vivo / iQOO ──
    {"device_model": "vivo X100 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "vivo X100", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "vivo X90 Pro+", "system_version": "Android 13", "app_version": "10.13.2", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "iQOO 12 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "iQOO Neo 9 Pro", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    # ── Huawei / Honor ──
    {"device_model": "Huawei P60 Pro", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Huawei Mate 60 Pro", "system_version": "Android 13", "app_version": "10.12.1", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Huawei Mate 60", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Honor Magic6 Pro", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Honor Magic5 Pro", "system_version": "Android 13", "app_version": "10.13.1", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Honor 90 Pro", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "en", "system_lang_code": "en-US"},
    # ── 其他 Android ──
    {"device_model": "Sony Xperia 1 VI", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "ja", "system_lang_code": "ja-JP"},
    {"device_model": "Sony Xperia 1 V", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "ja", "system_lang_code": "ja-JP"},
    {"device_model": "Sony Xperia 5 V", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Motorola Edge 40 Pro", "system_version": "Android 14", "app_version": "10.14.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Motorola Edge 50 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Nothing Phone (2)", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Nothing Phone (2a)", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "ASUS ROG Phone 8 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "ASUS ROG Phone 7", "system_version": "Android 13", "app_version": "10.13.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "ASUS Zenfone 10", "system_version": "Android 14", "app_version": "10.14.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "LG Velvet", "system_version": "Android 13", "app_version": "10.12.0", "lang_code": "ko", "system_lang_code": "ko-KR"},
    {"device_model": "LG V60 ThinQ", "system_version": "Android 13", "app_version": "10.11.0", "lang_code": "ko", "system_lang_code": "ko-KR"},
    {"device_model": "Nokia X30", "system_version": "Android 13", "app_version": "10.12.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "ZTE Axon 60 Ultra", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "Meizu 21 Pro", "system_version": "Android 14", "app_version": "10.14.3", "lang_code": "zh", "system_lang_code": "zh-CN"},
    # ── iPhone ──
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15 Pro", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15 Plus", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14 Plus", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 14", "system_version": "iOS 17.2", "app_version": "10.7.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 13 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 13 Pro", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 13", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 13 mini", "system_version": "iOS 17.2", "app_version": "10.7.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 12 Pro Max", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 12 Pro", "system_version": "iOS 17.2", "app_version": "10.7.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 12", "system_version": "iOS 17.2", "app_version": "10.7.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone SE 3", "system_version": "iOS 17.1", "app_version": "10.7.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 11 Pro Max", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 11 Pro", "system_version": "iOS 17.2", "app_version": "10.7.2", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 11", "system_version": "iOS 17.1", "app_version": "10.7.0", "lang_code": "en", "system_lang_code": "en-US"},
    # ── iPad ──
    {"device_model": "iPad Pro 12.9 (6th gen)", "system_version": "iPadOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPad Pro 11 (4th gen)", "system_version": "iPadOS 17.4", "app_version": "10.8.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPad Air (5th gen)", "system_version": "iPadOS 17.3", "app_version": "10.8.0", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPad mini (6th gen)", "system_version": "iPadOS 17.2", "app_version": "10.7.2", "lang_code": "en", "system_lang_code": "en-US"},
    # ── 多语言变体 (同型号不同语言) ──
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "de", "system_lang_code": "de-DE"},
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "fr", "system_lang_code": "fr-FR"},
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "es", "system_lang_code": "es-ES"},
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "pt", "system_lang_code": "pt-BR"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "zh", "system_lang_code": "zh-CN"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "ja", "system_lang_code": "ja-JP"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "ko", "system_lang_code": "ko-KR"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.4", "app_version": "10.8.1", "lang_code": "de", "system_lang_code": "de-DE"},
    {"device_model": "Google Pixel 8 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "de", "system_lang_code": "de-DE"},
    {"device_model": "Google Pixel 8 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "fr", "system_lang_code": "fr-FR"},
    {"device_model": "OnePlus 12", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "de", "system_lang_code": "de-DE"},
    {"device_model": "Xiaomi 14 Pro", "system_version": "Android 14", "app_version": "10.14.5", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "ru", "system_lang_code": "ru-RU"},
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 14", "app_version": "10.14.4", "lang_code": "ar", "system_lang_code": "ar-SA"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "ru", "system_lang_code": "ru-RU"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 17.3", "app_version": "10.8.0", "lang_code": "ar", "system_lang_code": "ar-SA"},
]
