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
    list_list_values, get_setting, _now_ts, get_proxy, set_proxy_enabled,
    update_proxy_check, get_account_proxy_id, clear_account_proxy_id
)
from app.core.db_remote import insert_members, insert_chat_messages, fetch_members, fetch_chat_messages, delete_proxy
from app.core.v2ray import V2RayController

# Global keepalive control
_keepalive_clients: list[Client] = []
_keepalive_stop_event = asyncio.Event()
_keepalive_task: Optional[asyncio.Task] = None

_auto_clients: dict[str, Client] = {}
_auto_stop_event = asyncio.Event()
_auto_task: Optional[asyncio.Task] = None
_auto_retry_counts: dict[str, int] = {}
_auto_blocked: set[str] = set()
_auto_max_retries = 3

async def _connect_clients(phones: list[str]) -> list[Client]:
    clients = []
    sem = asyncio.Semaphore(10)
    
    async def connect_one(phone):
        async with sem:
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return None
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = TelegramPanel.build_client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy, phone=phone)
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

def _auto_busy() -> bool:
    return bool(state.status or state.extract or state.chat_active or state.current_task_id)

def _get_max_concurrent() -> int:
    raw = get_setting("max_concurrent", "")
    val = None
    if raw is not None:
        s = str(raw).strip()
        if s:
            try:
                val = int(s)
            except Exception:
                val = None
    if not val:
        val = int(state.max_concurrent or 0)
    if val < 0:
        val = 0
    return val

def _apply_max_concurrent(phones: list[str], number_account: int) -> tuple[list[str], int, int]:
    max_c = _get_max_concurrent()
    if max_c > 0:
        if number_account > 0:
            number_account = min(number_account, max_c)
        else:
            number_account = max_c
    if number_account > 0:
        phones = phones[:number_account]
    return phones, number_account, max_c

async def _check_proxy_ok(proxy: dict) -> bool:
    if not proxy:
        return False
    if proxy.get("raw_url"):
        loop = asyncio.get_running_loop()
        port, err = await loop.run_in_executor(None, V2RayController.start, proxy["raw_url"])
        if err:
            return False
        ok = await TelegramPanel.check_proxy("127.0.0.1", port, "", "")
        V2RayController.stop(port)
        return ok
    return await TelegramPanel.check_proxy(proxy["host"], proxy["port"], proxy.get("username"), proxy.get("password"))

async def _handle_proxy_failure(phone: str):
    proxy_id = get_account_proxy_id(phone)
    if not proxy_id:
        return
    proxy = get_proxy(proxy_id)
    if not proxy:
        return
    ok = await _check_proxy_ok(proxy)
    update_proxy_check(proxy_id, 1 if ok else 0)
    if ok:
        return
    set_proxy_enabled(proxy_id, 0)
    delete_proxy(proxy)
    clear_account_proxy_id(phone)

async def _connect_auto_client(phone: str) -> Optional[Client]:
    if phone in _auto_blocked:
        return None
    data = TelegramPanel.get_json_data(phone)
    if not data:
        return None
    retries = _auto_retry_counts.get(phone, 0)
    while retries < _auto_max_retries:
        proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
        cli = TelegramPanel.build_client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy, phone=phone)
        try:
            await asyncio.wait_for(cli.connect(), 15)
            _auto_retry_counts[phone] = 0
            _auto_blocked.discard(phone)
            return cli
        except Exception:
            await TelegramPanel._safe_disconnect(cli, phone)
            await _handle_proxy_failure(phone)
            retries += 1
            _auto_retry_counts[phone] = retries
    _auto_blocked.add(phone)
    return None

async def _disconnect_auto_clients():
    for phone, cli in list(_auto_clients.items()):
        await TelegramPanel._safe_disconnect(cli, phone)
    _auto_clients.clear()

async def _auto_online_loop():
    state.auto_online = True
    state.auto_warmup = True
    while not _auto_stop_event.is_set():
        if _auto_busy():
            state.auto_warmup = False
            if _auto_clients:
                await _disconnect_auto_clients()
            await asyncio.sleep(5)
            continue
        state.auto_warmup = True
        phones = TelegramPanel.list_accounts()
        if not phones:
            await asyncio.sleep(10)
            continue
        for phone in list(_auto_clients.keys()):
            if phone not in phones:
                cli = _auto_clients.pop(phone)
                await TelegramPanel._safe_disconnect(cli, phone)
                _auto_retry_counts.pop(phone, None)
                _auto_blocked.discard(phone)
        for phone in phones:
            if phone not in _auto_clients:
                cli = await _connect_auto_client(phone)
                if cli:
                    _auto_clients[phone] = cli
        if not _auto_clients:
            await asyncio.sleep(5)
            continue
        cli = random.choice(list(_auto_clients.values()))
        try:
            await cli.get_me()
            action = random.choice(["scroll", "read"])
            await TelegramPanel.warmup_action(cli, action)
        except Exception:
            drop_phone = None
            for p, c in _auto_clients.items():
                if c == cli:
                    drop_phone = p
                    break
            if drop_phone:
                await TelegramPanel._safe_disconnect(cli, drop_phone)
                _auto_clients.pop(drop_phone, None)
        await asyncio.sleep(random.uniform(300, 600))

async def start_auto_online():
    global _auto_task
    if _auto_task and not _auto_task.done():
        return
    _auto_stop_event.clear()
    _auto_task = asyncio.create_task(_auto_online_loop())

async def stop_auto_online():
    global _auto_task
    _auto_stop_event.set()
    if _auto_task:
        try:
            await _auto_task
        except asyncio.CancelledError:
            pass
    await _disconnect_auto_clients()
    state.auto_online = False
    state.auto_warmup = False

async def extract_process(task_id: int, payload: dict):
    state.extract = True
    state.extract_running = 0
    links = payload.get("links") or []
    link = payload.get("link")
    if link:
        links.append(link)
    links = [l for l in links if str(l).strip()]
    if not links:
        append_task_log(task_id, "No links provided")
        state.extract = False
        return
    append_task_log(task_id, f"Starting extract from {len(links)} links")
    
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
        include_keywords = [k.lower() for k in (payload.get("include_keywords") or []) if k]
        exclude_keywords = [k.lower() for k in (payload.get("exclude_keywords") or []) if k]
        exclude_admin = bool(payload.get("exclude_admin"))
        exclude_bot = payload.get("exclude_bot")
        if exclude_bot is None:
            exclude_bot = True
        save_remote = payload.get("use_remote_db")
        if save_remote is None:
            save_remote = True
        all_usernames = []
        for link in links:
            if not state.extract:
                break
            chat = await cli.get_chat(link)
            append_task_log(task_id, f"Joined/Found chat: {chat.title}")
            members = []
            async for m in cli.get_chat_members(chat.id):
                if not state.extract:
                    break
                if m.user.is_deleted:
                    continue
                if exclude_bot and m.user.is_bot:
                    continue
                if exclude_admin and m.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
                    continue
                u_name = m.user.username or ""
                text = f"{u_name} {m.user.first_name or ''} {m.user.last_name or ''}".lower()
                if include_keywords and not any(k in text for k in include_keywords):
                    continue
                if exclude_keywords and any(k in text for k in exclude_keywords):
                    continue
                members.append({
                    "username": u_name
                })
                if u_name:
                    all_usernames.append(u_name)
                count += 1
                if count % 100 == 0:
                    state.extract_running = count
            if members and save_remote:
                insert_members(members)
                append_task_log(task_id, f"Inserted {len(members)} to remote DB")
            if members:
                os.makedirs("gaps", exist_ok=True)
                fname = f"gaps/{chat.title}_{int(time.time())}.txt"
                with open(fname, "w", encoding="utf-8") as f:
                    for mem in members:
                        if mem["username"]:
                            f.write(f"@{mem['username']}\n")
                append_task_log(task_id, f"Saved {len(members)} to {fname}")
        if payload.get("auto_load"):
            state.members = _normalize_usernames(all_usernames)
    except Exception as e:
        append_task_log(task_id, f"Error: {str(e)}")
        traceback.print_exc()
    finally:
        await TelegramPanel._safe_disconnect(cli)
        state.extract = False
        state.extract_running = 0

async def scrape_process(task_id: int, payload: dict):
    link = payload.get("link")
    limit = int(payload.get("limit") or 100)
    min_length = int(payload.get("min_length") or 0)
    keywords_blacklist = [k.lower() for k in (payload.get("keywords_blacklist") or []) if k]
    if not link:
        append_task_log(task_id, "No link provided")
        return
    phones = TelegramPanel.list_accounts()
    if not phones:
        append_task_log(task_id, "No accounts available for scrape")
        return
    cli_list = await _connect_clients(phones[:1])
    if not cli_list:
        append_task_log(task_id, "Failed to connect client")
        return
    cli = cli_list[0]
    try:
        chat = await cli.get_chat(link)
        messages = []
        async for msg in cli.get_chat_history(chat.id, limit=limit):
            content = msg.text or msg.caption or ""
            if not content:
                continue
            if min_length and len(content) < min_length:
                continue
            if keywords_blacklist and any(k in content.lower() for k in keywords_blacklist):
                continue
            sender = msg.from_user
            messages.append({
                "content": content
            })
        save_remote = payload.get("save_to_remote")
        if save_remote is None:
            save_remote = True
        if messages and save_remote:
            insert_chat_messages(messages)
            append_task_log(task_id, f"Inserted {len(messages)} messages to remote DB")
        append_task_log(task_id, f"Scraped {len(messages)} messages")
    except Exception as e:
        append_task_log(task_id, f"Scrape error: {e}")
    finally:
        await TelegramPanel._safe_disconnect(cli)

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
    batch_size = int(payload.get("batch_size") or 0)
    account_delay = int(payload.get("account_delay") or 300)
    links = [l for l in links if str(l).strip()]
    if not links:
        append_task_log(task_id, "No links provided")
        return
    
    phones = TelegramPanel.list_accounts()
    phones, number_account, max_c = _apply_max_concurrent(phones, number_account)
    if not phones:
        append_task_log(task_id, "No accounts available for join")
        return
    if batch_size <= 0:
        batch_size = len(phones)
    if max_c > 0:
        batch_size = min(batch_size, max_c)
    if account_delay < 0:
        account_delay = 0
    
    for start in range(0, len(phones), batch_size):
        batch = phones[start:start + batch_size]
        clients = await _connect_clients(batch)
        if not clients:
            append_task_log(task_id, "Failed to connect any account")
            continue
        try:
            for link in links:
                for cli in clients:
                    res = await TelegramPanel.join_chat(cli, link)
                    if res.get("ok"):
                        append_task_log(task_id, f"Joined {res.get('title') or ''} {res.get('link')}")
                    else:
                        append_task_log(task_id, f"Join failed {link}: {res.get('error')}")
                    if account_delay > 0:
                        await asyncio.sleep(account_delay)
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
    
    use_remote_db = bool(payload.get("use_remote_db"))
    if use_remote_db:
        limit = int(get_setting("max_members_limit") or 2000)
        targets = fetch_members(limit=limit)
    else:
        targets = _collect_usernames(group_names, use_loaded)
    targets = _apply_list_filters(targets)
    if not targets:
        append_task_log(task_id, "No members to invite")
        return
    if number_add <= 0:
        number_add = len(targets)
    
    phones = TelegramPanel.list_accounts()
    phones, number_account, _ = _apply_max_concurrent(phones, number_account)
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
                    record_member_added(username, "", task_id)
                    append_task_log(task_id, f"Invite failed {username}: {e}")
                await asyncio.sleep(random.uniform(300, 600))
    finally:
        for cli in clients:
            await TelegramPanel._safe_disconnect(cli)

async def chat_process(task_id: int, payload: dict):
    links = payload.get("links") or []
    if not links:
        link = payload.get("link")
        if link:
            links = [link]
    messages = payload.get("messages") or []
    number_account = int(payload.get("number_account") or 1)
    min_delay = int(payload.get("min_delay") or 300)
    max_delay = int(payload.get("max_delay") or max(min_delay, 600))
    max_messages = int(payload.get("max_messages") or 1)
    use_remote_db = bool(payload.get("use_remote_db"))
    if not links:
        append_task_log(task_id, "No target link provided")
        return
    if max_delay < min_delay:
        max_delay = min_delay
    
    phones = TelegramPanel.list_accounts()
    phones, number_account, _ = _apply_max_concurrent(phones, number_account)
    if not phones:
        append_task_log(task_id, "No accounts available for chat")
        return
    
    clients = await _connect_clients(phones)
    if not clients:
        append_task_log(task_id, "Failed to connect any account")
        return
    
    state.chat_active = True
    try:
        for link in links:
            remote_loaded = False
            for cli in clients:
                res = await TelegramPanel.join_chat(cli, link)
                if not res.get("ok"):
                    append_task_log(task_id, f"Join chat failed: {res.get('error')}")
                    continue
                chat_id = res.get("id")
                messages_pool = list(messages)
                if use_remote_db and not remote_loaded:
                    remote_messages = fetch_chat_messages(limit=max_messages * number_account)
                    if remote_messages:
                        messages_pool.extend(remote_messages)
                    remote_loaded = True
                if not messages_pool:
                    append_task_log(task_id, "No messages provided")
                    return
                count = 0
                while count < max_messages:
                    msg = random.choice(messages_pool)
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
    min_delay = int(payload.get("min_delay") or 300)
    max_delay = int(payload.get("max_delay") or max(min_delay, 600))
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
    phones, number_account, _ = _apply_max_concurrent(phones, number_account)
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
        links = payload.get("links") or []
        if not links:
            link = payload.get("link")
            if link:
                links = [link]
        number_add = int(payload.get("number_add") or 0)
        number_account = int(payload.get("number_account") or 0)
        min_delay = int(payload.get("min_delay") or 300)
        max_delay = int(payload.get("max_delay") or max(min_delay, 600))
        use_remote_db = bool(payload.get("use_remote_db")) or payload.get("use_remote_db") is None
        if not links:
            append_task_log(task_id, "No target link provided")
            return
        if max_delay < min_delay:
            max_delay = min_delay
        if use_remote_db:
            limit = int(get_setting("max_members_limit") or 2000)
            targets = fetch_members(limit=limit)
        else:
            targets = _normalize_usernames(state.members)
        targets = _apply_list_filters(targets)
        if not targets:
            append_task_log(task_id, "No members to add")
            return
        if number_add <= 0:
            number_add = len(targets)
        
        phones = TelegramPanel.list_accounts()
        phones, number_account, _ = _apply_max_concurrent(phones, number_account)
        if not phones:
            append_task_log(task_id, "No accounts available for adder")
            return
        
        clients = await _connect_clients(phones)
        if not clients:
            append_task_log(task_id, "Failed to connect any account")
            return
        
        try:
            for link in links:
                chat_id = None
                for cli in clients:
                    res = await TelegramPanel.join_chat(cli, link)
                    if res.get("ok"):
                        chat_id = res.get("id")
                        break
                if not chat_id:
                    append_task_log(task_id, "Failed to resolve target chat")
                    continue
                
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
                            record_member_added(username, "", task_id)
                            state.bad_count += 1
                            append_task_log(task_id, f"Add failed {username}: {e}")
                        await asyncio.sleep(random.uniform(min_delay, max_delay))
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
    run_at = task.get("run_at")
    
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
            await extract_process(task_id, payload)
        elif t_type == "scrape":
            await scrape_process(task_id, payload)
        else:
            append_task_log(task_id, f"Unknown task type {t_type}")
            
        update_task_status(task_id, "done", finished_at=_now_ts())
    except Exception as e:
        append_task_log(task_id, f"Task failed: {e}")
        update_task_status(task_id, "failed", finished_at=_now_ts())
    finally:
        if payload.get("run_daily"):
            next_run_at = (run_at or _now_ts()) + 86400
            create_task(t_type, payload, next_run_at)
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
