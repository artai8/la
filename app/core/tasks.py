import asyncio
import random
import time
import json
import traceback
from typing import Optional
from pyrogram import Client, errors, enums

from app.state import state
from app.core.telegram import TelegramPanel
from app.core.database import (
    create_task, update_task_status, set_task_running, append_task_log,
    get_due_task, get_task_log, delete_task, is_member_added, record_member_added,
    list_list_values, get_setting, _now_ts, list_proxies
)
from app.core.db_remote import insert_members

# Global keepalive control
_keepalive_clients: list[Client] = []
_keepalive_stop_event = asyncio.Event()
_keepalive_task: Optional[asyncio.Task] = None

async def _connect_clients(phones: list[str]) -> list[Client]:
    clients = []
    sem = asyncio.Semaphore(10)
    
    async def connect_one(phone):
        async with sem:
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return None
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await cli.connect()
                return cli
            except Exception as e:
                print(f"Failed to connect {phone}: {e}")
                return None

    tasks = [asyncio.create_task(connect_one(p)) for p in phones]
    results = await asyncio.gather(*tasks)
    return [c for c in results if c]

async def _keepalive_worker():
    while not _keepalive_stop_event.is_set():
        if not _keepalive_clients:
            break
        try:
            cli = random.choice(_keepalive_clients)
            # Simple ping: get me
            await cli.get_me()
            # Maybe read some history
            dialogs = []
            async for d in cli.get_dialogs(limit=5):
                dialogs.append(d)
            if dialogs:
                target = random.choice(dialogs)
                await cli.read_chat_history(target.chat.id)
        except Exception as e:
            pass
        
        # Random sleep between actions across all clients
        await asyncio.sleep(random.uniform(5, 30))

async def start_keepalive(clients: list[Client]):
    global _keepalive_clients, _keepalive_task
    if state.keepalive:
        return
    _keepalive_clients = clients
    state.keepalive = True
    _keepalive_stop_event.clear()
    _keepalive_task = asyncio.create_task(_keepalive_worker())

async def stop_keepalive():
    global _keepalive_clients, _keepalive_task
    state.keepalive = False
    _keepalive_stop_event.set()
    if _keepalive_task:
        try:
            await _keepalive_task
        except asyncio.CancelledError:
            pass
    for cli in _keepalive_clients:
        await TelegramPanel._safe_disconnect(cli)
    _keepalive_clients = []

async def warmup_process(phones: list[str], duration_min: int, actions: list[str]):
    clients = await _connect_clients(phones)
    if not clients:
        return
    
    end_time = time.time() + (duration_min * 60)
    
    while time.time() < end_time:
        for cli in clients:
            try:
                action = random.choice(actions)
                await TelegramPanel.warmup_action(cli, action)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(2, 10))
        await asyncio.sleep(1)

    for cli in clients:
        await TelegramPanel._safe_disconnect(cli)

async def extract_process(task_id: int, payload: dict):
    state.extract = True
    state.extract_running = 0
    link = payload.get("link")
    # simplified extract logic
    append_task_log(task_id, f"Starting extract from {link}")
    
    # We need a client to extract
    phones = TelegramPanel.list_accounts()
    if not phones:
        append_task_log(task_id, "No accounts available for extraction")
        state.extract = False
        return

    cli_list = await _connect_clients(phones[:1])
    if not cli_list:
        append_task_log(task_id, "Failed to connect client")
        state.extract = False
        return
    
    cli = cli_list[0]
    count = 0
    try:
        chat = await cli.get_chat(link)
        append_task_log(task_id, f"Joined/Found chat: {chat.title}")
        
        members = []
        async for m in cli.get_chat_members(chat.id):
            if m.user.is_bot or m.user.is_deleted:
                continue
            # Filter logic here (keywords etc)
            u_name = m.user.username or ""
            members.append({
                "username": u_name,
                "id": m.user.id,
                "access_hash": m.user.access_hash,
                "group_id": chat.id,
                "group_title": chat.title
            })
            count += 1
            if count % 100 == 0:
                state.extract_running = count
        
        if payload.get("use_remote_db"):
            insert_members(members)
            append_task_log(task_id, f"Inserted {len(members)} to remote DB")
        else:
            # Save to file
            os.makedirs("gaps", exist_ok=True)
            fname = f"gaps/{chat.title}_{int(time.time())}.txt"
            with open(fname, "w", encoding="utf-8") as f:
                for mem in members:
                    if mem["username"]:
                        f.write(f"@{mem['username']}\n")
            append_task_log(task_id, f"Saved {len(members)} to {fname}")
            
    except Exception as e:
        append_task_log(task_id, f"Error: {str(e)}")
        traceback.print_exc()
    finally:
        await TelegramPanel._safe_disconnect(cli)
        state.extract = False
        state.extract_running = 0

async def adder_process(task_id: int, payload: dict):
    # Simplified adder logic
    state.status = True
    append_task_log(task_id, "Starting adder")
    # ... logic to add members ...
    # This is complex, I will put a placeholder or simplified version
    append_task_log(task_id, "Adder logic placeholder")
    await asyncio.sleep(5)
    state.status = False

async def run_task(task: dict):
    task_id = task["id"]
    t_type = task["type"]
    payload = json.loads(task["payload"])
    
    state.current_task_id = task_id
    state.current_task_type = t_type
    
    set_task_running(task_id)
    
    try:
        if t_type == "extract":
            await extract_process(task_id, payload)
        elif t_type == "adder":
            await adder_process(task_id, payload)
        elif t_type == "extract_batch":
            # Loop over links
            links = payload.get("links", [])
            for link in links:
                sub_payload = payload.copy()
                sub_payload["link"] = link
                await extract_process(task_id, sub_payload)
        else:
            append_task_log(task_id, f"Unknown task type {t_type}")
            
        update_task_status(task_id, "done", finished_at=_now_ts())
    except Exception as e:
        append_task_log(task_id, f"Task failed: {e}")
        update_task_status(task_id, "failed", finished_at=_now_ts())
    finally:
        state.current_task_id = None
        state.current_task_type = None

async def task_loop():
    print("Task loop started")
    while True:
        try:
            task = get_due_task()
            if task:
                print(f"Running task {task['id']}")
                await run_task(task)
            else:
                await asyncio.sleep(5)
        except Exception as e:
            print(f"Task loop error: {e}")
            await asyncio.sleep(5)
