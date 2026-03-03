"""数据库连接 - 本地 PostgreSQL + Supabase 远程"""
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from supabase import create_client, Client as SupabaseClient

from app.config import DATABASE_URL, settings

logger = logging.getLogger(__name__)

# ---- 本地 PostgreSQL (SQLAlchemy async) ----
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,       # 自动检测断开的连接
    pool_recycle=1800,        # 30分钟回收连接
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入: 获取数据库session"""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """初始化数据库表"""
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表初始化完成")


# ---- Supabase 远程数据库 ----
_supabase_client: SupabaseClient | None = None


def get_supabase(url: str = "", key: str = "") -> SupabaseClient | None:
    """获取 Supabase 客户端（懒加载）"""
    global _supabase_client
    if url and key:
        _supabase_client = create_client(url, key)
        logger.info("Supabase 客户端已初始化")
    return _supabase_client


def reset_supabase():
    """重置 Supabase 客户端"""
    global _supabase_client
    _supabase_client = None


async def init_supabase_from_db():
    """从本地 settings 表读取 Supabase 配置并初始化"""
    from sqlalchemy import select
    from app.models import Setting
    async with async_session_factory() as session:
        result = await session.execute(
            select(Setting).where(Setting.key.in_(["supabase_url", "supabase_key"]))
        )
        settings = {s.key: s.value for s in result.scalars().all()}
        url = settings.get("supabase_url", "")
        key = settings.get("supabase_key", "")
        if url and key:
            get_supabase(url, key)
            logger.info("从数据库加载 Supabase 配置成功")
        else:
            logger.info("未配置 Supabase，跳过远程数据库初始化")
