import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

from app.config import settings as app_settings
from app.database import init_db, init_supabase_from_db, engine
from app.services.telegram_client import disconnect_all, get_connected_count
from app.services.proxy_manager import stop_all_xray
from app.services.task_scheduler import start_scheduler, shutdown_scheduler, restore_tasks
from app.services.session_restorer import restore_all_sessions

from app.routers import settings as settings_router
from app.routers import accounts as accounts_router
from app.routers import scraping as scraping_router
from app.routers import operations as operations_router
from app.routers import tasks as tasks_router
from app.routers import logs as logs_router

# ──────────────── Logging ────────────────
os.makedirs(app_settings.LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(app_settings.LOG_DIR, "app.log"), encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("tg-manager")


# ──────────────── Lifespan ────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / Shutdown lifecycle."""
    logger.info("🚀 应用启动中 ...")

    # 1. 初始化数据库表
    await init_db()
    logger.info("✅ 数据库初始化完成")

    # 2. 从 DB 加载 Supabase 配置（如有）
    try:
        await init_supabase_from_db()
        logger.info("✅ Supabase 配置已加载")
    except Exception as e:
        logger.warning(f"⚠️ Supabase 配置加载跳过: {e}")

    # 3. 恢复定时任务 & 启动调度器
    await restore_tasks()
    start_scheduler()
    logger.info("✅ 定时任务调度器已启动")

    # 4. 分批恢复 Telegram 客户端 Session
    try:
        restored, failed = await restore_all_sessions()
        logger.info(f"✅ Session 恢复完成: 成功 {restored}, 失败 {failed}")
    except Exception as e:
        logger.warning(f"⚠️ Session 恢复出错: {e}")

    yield  # ─── 运行中 ───

    # Shutdown
    logger.info("🛑 应用关闭中 ...")
    shutdown_scheduler()
    await disconnect_all()
    stop_all_xray()
    await engine.dispose()
    logger.info("👋 关闭完成")


# ──────────────── App ────────────────
app = FastAPI(
    title="Telegram Multi-Account Manager",
    version="1.0.0",
    lifespan=lifespan,
)

# 静态文件 & 模板
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.state.templates = templates

# 路由注册
app.include_router(settings_router.router)
app.include_router(accounts_router.router)
app.include_router(scraping_router.router)
app.include_router(operations_router.router)
app.include_router(tasks_router.router)
app.include_router(logs_router.router)


# ──────────────── Root & Health ────────────────
@app.get("/")
async def root():
    """Redirect to account list as default home page."""
    return RedirectResponse(url="/accounts/", status_code=302)


@app.get("/health")
async def health():
    """Health check endpoint for Railway — 含客户端、调度器、熔断器详细状态."""
    from app.services.circuit_breaker import circuit_breaker
    from app.services.task_scheduler import scheduler

    cb_open = circuit_breaker.is_open()
    return {
        "status": "degraded" if cb_open else "ok",
        "connected_clients": get_connected_count(),
        "scheduler_running": scheduler.running,
        "scheduler_jobs": len(scheduler.get_jobs()),
        "circuit_breaker": {
            "open": cb_open,
            "remaining_seconds": round(circuit_breaker.remaining_seconds(), 1) if cb_open else 0,
            "recent_events": len(circuit_breaker._events),
        },
    }
