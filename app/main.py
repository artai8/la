import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db, upsert_worker
from app.state import state
from app.core.telegram import TelegramPanel
from app.core.tasks import task_loop

from app.routers import pages, auth, accounts, extract, adder, settings, lists, users, tasks, reports, ws

app = FastAPI(title="Telegram Adder Panel")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(extract.router)
app.include_router(adder.router)
app.include_router(settings.router)
app.include_router(lists.router)
app.include_router(users.router)
app.include_router(tasks.router)
app.include_router(reports.router)
app.include_router(ws.router)

@app.on_event("startup")
async def on_startup():
    init_db()
    upsert_worker("local", "online")
    state.max_concurrent = TelegramPanel.get_max_concurrent()
    print(f"Max concurrent: {state.max_concurrent}")
    asyncio.create_task(task_loop())
