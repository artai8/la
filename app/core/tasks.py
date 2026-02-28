import asyncio
import os
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

def _normalize_usernames(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for it in items:
        if not it:
            continue
        name = str(it).strip()
        if name.startswith("@"):
            name = name[1:]
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out

def _collect_usernames(group_names: list[str], use_loaded: bool) -> list[str]:
    items = []
    if use_loaded and state.members:
        items.extend(state.members)
    for g in group_names:
        items.extend(TelegramPanel.load_group(g))
    return _normalize_usernames(items)

def _apply_list_filters(usernames: list[str]) -> list[str]:
    blacklist = set(_normalize_usernames(list_list_values("blacklist")))
    whitelist = set(_normalize_usernames(list_list_values("whitelist")))
    if not blacklist and not whitelist:
        return usernames
    out = []
    for name in usernames:
        if name in blacklist:
            continue
        if whitelist and name not in whitelist:
            continue
        out.append(name)
    return out

async def join_process(task_id: int, payload: dict):
    links = payload.get("links") or []
    number_account = int(payload.get("number_account") or 0)
    links = [l for l in links if str(l).strip()]
    if not links:
        append_task_log(task_id, "No links provided")
        return
    
    phones = TelegramPanel.list_accounts()
    if number_account > 0:
        phones = phones[:number_account]
    if not phones:
        append_task_log(task_id, "No accounts available for join")
        return
    
    clients = await _connect_clients(phones)
    if not clients:
        append_task_log(task_id, "Failed to connect any account")
        return
    
    try:
        for link in links:
            for cli in clients:
                res = await TelegramPanel.join_chat(cli, link)
                if res.get("ok"):
                    append_task_log(task_id, f"Joined {res.get('title') or ''} {res.get('link')}")
                else:
                    append_task_log(task_id, f"Join failed {link}: {res.get('error')}")
                await asyncio.sleep(random.uniform(1, 3))
    finally:
        for cli in clients:
            await TelegramPanel._safe_disconnect(cli)

async def invite_process(task_id: int, payload: dict):
    link = payload.get("link")
    group_names = payload.get("group_names") or []
    number_add = int(payload.get("number_add") or 0)
    number_account = int(payload.get("number_account") or 0)
    use_loaded = bool(payload.get("use_loaded"))
    if not link:
        append_task_log(task_id, "No target link provided")
        return
    
    targets = _collect_usernames(group_names, use_loaded)
    targets = _apply_list_filters(targets)
    if not targets:
        append_task_log(task_id, "No members to invite")
        return
    if number_add <= 0:
        number_add = len(targets)
    
    phones = TelegramPanel.list_accounts()
    if number_account > 0:
        phones = phones[:number_account]
    if not phones:
        append_task_log(task_id, "No accounts available for invite")
        return
    
    clients = await _connect_clients(phones)
    if not clients:
        append_task_log(task_id, "Failed to connect any account")
        return
    
    try:
        chat_id = None
        for cli in clients:
            res = await TelegramPanel.join_chat(cli, link)
            if res.get("ok"):
                chat_id = res.get("id")
                break
        if not chat_id:
            append_task_log(task_id, "Failed to resolve target chat")
            return
        
        target_queue = list(targets)
        random.shuffle(target_queue)
        for cli in clients:
            added = 0
            while target_queue and added < number_add:
                username = target_queue.pop(0)
                if is_member_added(username):
                    continue
                try:
                    await cli.add_chat_members(chat_id, username)
                    record_member_added(username, "", task_id)
                    append_task_log(task_id, f"Invited {username}")
                    added += 1
                except Exception as e:
                    append_task_log(task_id, f"Invite failed {username}: {e}")
                await asyncio.sleep(random.uniform(1, 3))
    finally:
        for cli in clients:
            await TelegramPanel._safe_disconnect(cli)

async def chat_process(task_id: int, payload: dict):
    link = payload.get("link")
    messages = payload.get("messages") or []
    number_account = int(payload.get("number_account") or 1)
    min_delay = int(payload.get("min_delay") or 1)
    max_delay = int(payload.get("max_delay") or max(min_delay, 1))
    max_messages = int(payload.get("max_messages") or 1)
    if not link:
        append_task_log(task_id, "No target link provided")
        return
    if not messages:
        append_task_log(task_id, "No messages provided")
        return
    if max_delay < min_delay:
        max_delay = min_delay
    
    phones = TelegramPanel.list_accounts()
    if number_account > 0:
        phones = phones[:number_account]
    if not phones:
        append_task_log(task_id, "No accounts available for chat")
        return
    
    clients = await _connect_clients(phones)
    if not clients:
        append_task_log(task_id, "Failed to connect any account")
        return
    
    state.chat_active = True
    try:
        for cli in clients:
            res = await TelegramPanel.join_chat(cli, link)
            if not res.get("ok"):
                append_task_log(task_id, f"Join chat failed: {res.get('error')}")
                continue
            chat_id = res.get("id")
            count = 0
            while count < max_messages:
                msg = random.choice(messages)
                try:
                    await cli.send_message(chat_id, msg)
                    append_task_log(task_id, f"Sent message {count + 1}")
                except Exception as e:
                    append_task_log(task_id, f"Send failed: {e}")
                count += 1
                await asyncio.sleep(random.uniform(min_delay, max_delay))
    finally:
        state.chat_active = False
        for cli in clients:
            await TelegramPanel._safe_disconnect(cli)

async def dm_process(task_id: int, payload: dict):
    group_name = payload.get("group_name") or ""
    messages = payload.get("messages") or []
    number_account = int(payload.get("number_account") or 1)
    min_delay = int(payload.get("min_delay") or 1)
    max_delay = int(payload.get("max_delay") or max(min_delay, 1))
    use_loaded = bool(payload.get("use_loaded"))
    if not messages:
        append_task_log(task_id, "No messages provided")
        return
    if max_delay < min_delay:
        max_delay = min_delay
    
    targets = _collect_usernames([group_name] if group_name else [], use_loaded)
    if not targets:
        append_task_log(task_id, "No members to message")
        return
    
    phones = TelegramPanel.list_accounts()
    if number_account > 0:
        phones = phones[:number_account]
    if not phones:
        append_task_log(task_id, "No accounts available for DM")
        return
    
    clients = await _connect_clients(phones)
    if not clients:
        append_task_log(task_id, "Failed to connect any account")
        return
    
    try:
        target_queue = list(targets)
        random.shuffle(target_queue)
        for cli in clients:
            while target_queue:
                username = target_queue.pop(0)
                msg = random.choice(messages)
                res = await TelegramPanel.send_dm(cli, username, msg)
                if res.get("ok"):
                    append_task_log(task_id, f"DM sent to {username}")
                else:
                    append_task_log(task_id, f"DM failed {username}: {res.get('error')}")
                await asyncio.sleep(random.uniform(min_delay, max_delay))
    finally:
        for cli in clients:
            await TelegramPanel._safe_disconnect(cli)

async def adder_process(task_id: int, payload: dict):
    state.status = True
    state.reset_adder()
    try:
        link = payload.get("link")
        number_add = int(payload.get("number_add") or 0)
        number_account = int(payload.get("number_account") or 0)
        if not link:
            append_task_log(task_id, "No target link provided")
            return
        
        targets = _normalize_usernames(state.members)
        targets = _apply_list_filters(targets)
        if not targets:
            append_task_log(task_id, "No members to add")
            return
        if number_add <= 0:
            number_add = len(targets)
        
        phones = TelegramPanel.list_accounts()
        if number_account > 0:
            phones = phones[:number_account]
        if not phones:
            append_task_log(task_id, "No accounts available for adder")
            return
        
        clients = await _connect_clients(phones)
        if not clients:
            append_task_log(task_id, "Failed to connect any account")
            return
        
        try:
            chat_id = None
            for cli in clients:
                res = await TelegramPanel.join_chat(cli, link)
                if res.get("ok"):
                    chat_id = res.get("id")
                    break
            if not chat_id:
                append_task_log(task_id, "Failed to resolve target chat")
                return
            
            target_queue = list(targets)
            random.shuffle(target_queue)
            for cli in clients:
                added = 0
                while target_queue and added < number_add and state.status:
                    username = target_queue.pop(0)
                    if is_member_added(username):
                        continue
                    try:
                        await cli.add_chat_members(chat_id, username)
                        record_member_added(username, "", task_id)
                        state.ok_count += 1
                        append_task_log(task_id, f"Added {username}")
                        added += 1
                    except Exception as e:
                        state.bad_count += 1
                        append_task_log(task_id, f"Add failed {username}: {e}")
                    await asyncio.sleep(random.uniform(1, 3))
                if not state.status:
                    break
        finally:
            for cli in clients:
                await TelegramPanel._safe_disconnect(cli)
    finally:
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
        elif t_type == "join":
            await join_process(task_id, payload)
        elif t_type == "invite":
            await invite_process(task_id, payload)
        elif t_type == "chat":
            await chat_process(task_id, payload)
        elif t_type == "dm":
            await dm_process(task_id, payload)
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
