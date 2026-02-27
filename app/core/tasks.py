import asyncio
import random
import json
import traceback
import shutil
import re
from datetime import datetime
from typing import Optional
from pyrogram import Client, errors, enums

from app.state import state
from app.core.ws import manager
from app.core.telegram import TelegramPanel
from app.models import _normalize_links, _normalize_keywords
from app.database import (
    get_due_task, set_task_running, update_task_status, append_task_log,
    list_list_values, is_member_added, record_member_added, get_setting
)
from app.core.db_remote import (
    save_member_remote, get_members_remote, is_member_invited_remote,
    mark_member_used_remote, save_chat_remote, get_chat_remote
)

_keepalive_clients = {}
_keepalive_tasks = []
_last_action_ts = {}

def _now_ts() -> float:
    return datetime.utcnow().timestamp()

async def _pace_before_action(phone: str, key: str, min_delay: int, max_delay: int):
    min_delay = max(1, int(min_delay))
    max_delay = max(min_delay, int(max_delay))
    base = random.uniform(min_delay, max_delay)
    bucket = _last_action_ts.setdefault(key, {})
    last = bucket.get(phone)
    if last:
        wait = base - (_now_ts() - last)
        if wait > 0:
            await asyncio.sleep(wait)
    else:
        await asyncio.sleep(random.uniform(0, min_delay))
    bucket[phone] = _now_ts()

def _move_to_delete(phone):
    for ext, src in [(".session", "account"), (".json", "data")]:
        try:
            shutil.move(f"{src}/{phone}{ext}", f"delete/{phone}{ext}")
        except FileNotFoundError:
            pass

def _match_username(username: str, include_keywords: list[str], exclude_keywords: list[str], blacklist: set, whitelist: set) -> bool:
    if username in blacklist:
        return False
    if whitelist and username not in whitelist:
        return False
    if include_keywords and not any(k in username for k in include_keywords):
        return False
    if exclude_keywords and any(k in username for k in exclude_keywords):
        return False
    return True

async def load_members_from_group(name: str):
    members = TelegramPanel.load_group(name)
    added = 0
    skipped = 0
    blacklist = set(list_list_values("blacklist"))
    whitelist = set(list_list_values("whitelist"))
    for m in members:
        if m in blacklist:
            skipped += 1
            continue
        if whitelist and m not in whitelist:
            skipped += 1
            continue
        if is_member_added(m):
            skipped += 1
            continue
        if m not in state.members:
            state.members.append(m)
            added += 1
    return added, skipped

async def load_members_from_groups(names: list[str]):
    t_added = 0
    t_skipped = 0
    for n in names:
        a, s = await load_members_from_group(n)
        t_added += a
        t_skipped += s
    return t_added, t_skipped

async def extract_process(link: str, include_keywords: list[str], exclude_keywords: list[str], auto_load: bool, account: Optional[str], manage_state: bool, use_remote_db: bool = False):
    cli = None
    phone = None
    try:
        phone = account or random.choice(TelegramPanel.list_accounts())
        await manager.log("extract", f"Account: {phone}")

        data = TelegramPanel.get_json_data(phone)
        proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
        cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)

        await asyncio.wait_for(cli.connect(), 15)
        await manager.log("extract", f"Connected: {phone}")

        join = await TelegramPanel.join_chat(cli, link)
        if not join["ok"]:
            await TelegramPanel._safe_disconnect(cli, phone)
            await manager.log("extract", f"Join failed: {join['error']}")
            if manage_state:
                state.extract = False
                state.extract_running = 0
                await manager.send_state()
            return

        chat = await cli.get_chat(join["id"])
        await manager.log("extract", f"Members: {chat.members_count}")

        if manage_state:
            state.members_ext = []
            state.extract_running = 1
        blacklist = set(list_list_values("blacklist"))
        whitelist = set(list_list_values("whitelist"))
        searches = list("abcdefghijklmnopqrstuvwxyz0123456789")

        async for r in cli.get_chat_members(chat.id, limit=chat.members_count,
                                             filter=enums.ChatMembersFilter.RECENT):
            if not state.extract:
                break
            try:
                u = r.user
                if r.status == enums.ChatMemberStatus.MEMBER and not u.is_bot and u.username:
                    if _match_username(u.username, include_keywords, exclude_keywords, blacklist, whitelist):
                        if u.username not in state.members_ext:
                            state.members_ext.append(u.username)
                            if use_remote_db:
                                save_member_remote(u.username, u.id, u.access_hash, link)
                            if auto_load and not is_member_added(u.username) and u.username not in state.members:
                                state.members.append(u.username)
                            await manager.log("extract", f"[{len(state.members_ext)}] {u.username}")
                            await manager.send_state()
                            await asyncio.sleep(0.1)
            except Exception:
                pass

        for q in searches:
            if not state.extract:
                break
            async for r in cli.get_chat_members(chat.id, q, chat.members_count,
                                                  filter=enums.ChatMembersFilter.SEARCH):
                if not state.extract:
                    break
                try:
                    u = r.user
                    if r.status == enums.ChatMemberStatus.MEMBER and not u.is_bot and u.username:
                        if _match_username(u.username, include_keywords, exclude_keywords, blacklist, whitelist):
                            if u.username not in state.members_ext:
                                state.members_ext.append(u.username)
                                if use_remote_db:
                                    save_member_remote(u.username, u.id, u.access_hash, link)
                                if auto_load and not is_member_added(u.username) and u.username not in state.members:
                                    state.members.append(u.username)
                                await manager.log("extract", f"[{len(state.members_ext)}] {u.username}")
                except Exception:
                    pass

        await cli.disconnect()

        if state.members_ext and not use_remote_db:
            name = link.split("/")[-1] if not link.startswith("@") else link[1:]
            name = re.sub(r'[^\w\-+]', '_', name)
            with open(f"gaps/{name}.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(state.members_ext))

        await manager.log("extract", f"Done: {len(state.members_ext)} members")

    except Exception as e:
        traceback.print_exc()
        await manager.log("extract", f"Error: {e}")
    finally:
        if cli:
            await TelegramPanel._safe_disconnect(cli, phone)
        if manage_state:
            state.extract = False
            state.extract_running = 0
            await manager.send_state()

async def extract_batch_process(links: list[str], include_keywords: list[str], exclude_keywords: list[str], auto_load: bool, use_remote_db: bool = False):
    accs = TelegramPanel.list_accounts()
    if not accs:
        state.extract = False
        await manager.send_state()
        return
    state.extract_running = len(links)
    sem = asyncio.Semaphore(min(len(accs), len(links), 5))

    async def run_one(link: str, phone: str):
        async with sem:
            await extract_process(link, include_keywords, exclude_keywords, auto_load, phone, False, use_remote_db)
            state.extract_running -= 1
            await manager.send_state()

    tasks = []
    used_phones = set()
    for i, link in enumerate(links):
        phone = accs[i % len(accs)]
        # ensure unique account per concurrent task if possible, but semaphore handles concurrency limit
        tasks.append(asyncio.create_task(run_one(link, phone)))
    
    await asyncio.gather(*tasks)
    state.extract = False
    state.extract_running = 0
    await manager.send_state()

async def join_groups_process(links: list[str], accounts: list[str]):
    # Random delay per join to avoid flood
    sem = asyncio.Semaphore(5)
    
    async def run_one(phone):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await cli.connect()
                for link in links:
                    try:
                        await _pace_before_action(phone, "join", 5, 20)
                        await TelegramPanel.join_chat(cli, link)
                        await manager.log("join", f"{phone} joined {link}")
                    except Exception as e:
                        await manager.log("join", f"{phone} failed {link}: {e}")
            except Exception as e:
                await manager.log("join", f"{phone} error: {e}")
            finally:
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(run_one(phone)) for phone in accounts]
    if tasks:
        await asyncio.gather(*tasks)

async def chat_process(link: str, accounts: list[str], messages: list[str], min_delay: int, max_delay: int, max_messages: int, use_remote_db: bool = False):
    if not link or not accounts:
        return

    min_delay = max(1, int(min_delay))
    max_delay = max(min_delay, int(max_delay))
    
    async def run_one(phone):
        cli = None
        data = TelegramPanel.get_json_data(phone)
        if not data: return
        proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
        cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
        try:
            await asyncio.wait_for(cli.connect(), 15)
            chat = await TelegramPanel.join_chat(cli, link)
            if not chat["ok"]:
                await manager.log("chat", f"Join failed {link} - {phone}: {chat['error']}")
                return

            sent_count = 0
            while state.chat_active and sent_count < max_messages:
                try:
                    await _pace_before_action(phone, "chat", min_delay, max_delay)
                    msg = get_chat_remote() if use_remote_db else random.choice(messages)
                    if not msg:
                        if use_remote_db:
                            await manager.log("chat", "Remote DB2 empty")
                            break
                        msg = "Hi"
                    
                    await cli.send_message(chat["id"], msg)
                    sent_count += 1
                    await manager.log("chat", f"Sent {phone}: {msg[:10]}...")
                except Exception as e:
                    await manager.log("chat", f"Error {phone}: {e}")
                    if "FloodWait" in str(e):
                         await asyncio.sleep(30)
        except Exception as e:
            await manager.log("chat", f"Chat error {link} - {phone}: {e}")
        finally:
            await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(run_one(phone)) for phone in accounts]
    if tasks:
        await asyncio.gather(*tasks)

async def dm_process(group_name: str, accounts: list[str], messages: list[str], min_delay: int, max_delay: int):
    if not group_name or not accounts or not messages:
        return
    
    # Load targets
    targets = TelegramPanel.load_group(group_name)
    if not targets:
        await manager.log("dm", "No targets loaded")
        return

    # Filter targets (blacklist)
    blacklist = set(list_list_values("blacklist"))
    targets = [t for t in targets if t not in blacklist]
    
    min_delay = max(1, int(min_delay))
    max_delay = max(min_delay, int(max_delay))
    
    # Shared queue of targets
    queue = asyncio.Queue()
    for t in targets:
        queue.put_nowait(t)
        
    sem = asyncio.Semaphore(min(len(accounts), 10))
    state.status = True # Reuse global status flag for now

    async def worker(phone: str):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await asyncio.wait_for(cli.connect(), 15)
                while not queue.empty() and state.status:
                    try:
                        target = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                    await _pace_before_action(phone, "dm", min_delay, max_delay)
                    msg = random.choice(messages)
                    res = await TelegramPanel.send_dm(cli, target, msg)
                    if res["ok"]:
                        await manager.log("dm", f"Sent to {target} - {phone}")
                    else:
                        await manager.log("dm", f"Failed {target} - {phone}: {res['error']}")
                        # If FloodWait, sleep
                        if "FloodWait" in str(res.get("error")):
                             # Simple backoff, or just continue to next
                             await asyncio.sleep(random.randint(30, 60))
            except Exception as e:
                await manager.log("dm", f"Worker error {phone}: {e}")
            finally:
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(worker(p)) for p in accounts]
    if tasks:
        await asyncio.gather(*tasks)
    state.status = False

async def warmup_process(accounts: list[str], duration_min: int, actions: list[str]):
    if not accounts:
        return
    end_time = datetime.utcnow().timestamp() + (duration_min * 60)
    sem = asyncio.Semaphore(min(len(accounts), 10))
    state.status = True

    async def worker(phone: str):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            if not data:
                return
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await asyncio.wait_for(cli.connect(), 15)
                while datetime.utcnow().timestamp() < end_time and state.status:
                    await _pace_before_action(phone, "warmup", 10, 60)
                    act = random.choice(actions) if actions else "read"
                    res = await TelegramPanel.warmup_action(cli, act)
                    if res["ok"]:
                        await manager.log("warmup", f"{phone}: {res['action']}")
                    else:
                        await manager.log("warmup", f"{phone} error: {res['error']}")
            except Exception as e:
                await manager.log("warmup", f"Worker error {phone}: {e}")
            finally:
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(worker(p)) for p in accounts]
    if tasks:
        await asyncio.gather(*tasks)
    state.status = False

def _get_max_concurrent_setting() -> int:
    try:
        val = int(get_setting("max_concurrent", "0") or 0)
    except Exception:
        val = 0
    return state.max_concurrent if val <= 0 else val

async def stop_keepalive():
    global _keepalive_clients, _keepalive_tasks
    state.keepalive = False
    for t in list(_keepalive_tasks):
        t.cancel()
    _keepalive_tasks = []
    for phone, cli in list(_keepalive_clients.items()):
        try:
            await TelegramPanel._safe_disconnect(cli, phone)
        except Exception:
            pass
    _keepalive_clients = {}
    await manager.send_state()

async def _keepalive_worker(phone: str, cli: Client):
    while state.keepalive:
        try:
            if not cli.is_connected:
                await asyncio.wait_for(cli.connect(), 15)
            await asyncio.sleep(30)
        except Exception as e:
            await manager.log("adder", f"Keepalive error {phone}: {e}")
            await asyncio.sleep(30)

async def start_keepalive(clients: dict):
    await stop_keepalive()
    if not clients:
        return
    state.keepalive = True
    _keepalive_clients.update(clients)
    for phone, cli in clients.items():
        _keepalive_tasks.append(asyncio.create_task(_keepalive_worker(phone, cli)))
    await manager.send_state()

async def _connect_clients(accounts: list[str]) -> dict:
    clients = {}
    sem = asyncio.Semaphore(_get_max_concurrent_setting())

    async def connect_one(phone: str):
        async with sem:
            cli = None
            data = TelegramPanel.get_json_data(phone)
            if not data:
                await manager.log("adder", f"Missing data: {phone}")
                return
            proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
            cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)
            try:
                await asyncio.wait_for(cli.connect(), 15)
                clients[phone] = cli
            except Exception as e:
                await manager.log("adder", f"Connect failed {phone}: {e}")
                await TelegramPanel._safe_disconnect(cli, phone)

    tasks = [asyncio.create_task(connect_one(p)) for p in accounts]
    if tasks:
        await asyncio.gather(*tasks)
    return clients

async def sequence_process(chat_link: str, messages: list[str], min_delay: int, max_delay: int, chat_per_account: int,
                           pick_min: int, pick_max: int, add_link: str, group_names: list[str], use_loaded: bool,
                           adds_per_account: int, number_account: int, keep_online: bool, use_remote_db: bool = False, use_remote_content: bool = False):
    accs = TelegramPanel.list_accounts()
    if not accs:
        await manager.log("adder", "No accounts")
        return
    number_account = max(1, int(number_account or len(accs)))
    accounts = random.sample(accs, min(number_account, len(accs)))
    min_delay = max(1, int(min_delay))
    max_delay = max(min_delay, int(max_delay))
    chat_per_account = max(1, int(chat_per_account))
    adds_per_account = max(1, int(adds_per_account))
    pick_min = max(1, int(pick_min))
    pick_max = max(pick_min, int(pick_max))

    clients = await _connect_clients(accounts)
    if not clients:
        await manager.log("adder", "No clients connected")
        return

    chat_ids = {}
    for phone, cli in list(clients.items()):
        join = await TelegramPanel.join_chat(cli, chat_link)
        if not join["ok"]:
            await manager.log("adder", f"Chat join failed {chat_link} - {phone}: {join['error']}")
            await TelegramPanel._safe_disconnect(cli, phone)
            clients.pop(phone, None)
            continue
        chat_ids[phone] = join["id"]

    if not chat_ids:
        await manager.log("adder", "No chat joined")
        for phone, cli in clients.items():
            await TelegramPanel._safe_disconnect(cli, phone)
        return

    remaining_chat = {p: chat_per_account for p in chat_ids.keys()}
    while state.status and any(v > 0 for v in remaining_chat.values()):
        candidates = [p for p, v in remaining_chat.items() if v > 0]
        if not candidates:
            break
        k = min(random.randint(pick_min, pick_max), len(candidates))
        pick = random.sample(candidates, k)
        for phone in pick:
            cli = clients.get(phone)
            if not cli:
                remaining_chat[phone] = 0
                continue
            try:
                await _pace_before_action(phone, "sequence_chat", min_delay, max_delay)
                msg = get_chat_remote() if use_remote_content else random.choice(messages)
                if not msg and use_remote_content: msg = "Hi" # Fallback
                if not msg: msg = "Hi"
                
                await cli.send_message(chat_ids[phone], msg)
                sent = chat_per_account - remaining_chat[phone] + 1
                await manager.log("adder", f"Chat {phone} {sent}/{chat_per_account}")
            except Exception as e:
                await manager.log("adder", f"Chat error {phone}: {e}")
            remaining_chat[phone] -= 1

    if use_remote_db:
        # Load from remote DB1
        rows = get_members_remote(limit=number_account * adds_per_account * 2)
        state.members = [r["username"] for r in rows if r["username"]]
        # We need mapping user_id to update status later
        state._remote_map = {r["username"]: r["user_id"] for r in rows if r["username"]}
    elif group_names:
        state.members = []
        await load_members_from_groups(group_names)
    elif not use_loaded:
        state.members = []

    add_ids = {}
    for phone, cli in list(clients.items()):
        join = await TelegramPanel.join_chat(cli, add_link)
        if not join["ok"]:
            await manager.log("adder", f"Add join failed {add_link} - {phone}: {join['error']}")
            await TelegramPanel._safe_disconnect(cli, phone)
            clients.pop(phone, None)
            continue
        add_ids[phone] = join["id"]

    if not add_ids:
        await manager.log("adder", "No add target joined")
        for phone, cli in clients.items():
            await TelegramPanel._safe_disconnect(cli, phone)
        return

    flood_limit = int(get_setting("flood_wait_limit", "500") or 500)
    remaining_adds = {p: adds_per_account for p in add_ids.keys()}
    while state.status and any(v > 0 for v in remaining_adds.values()):
        candidates = [p for p, v in remaining_adds.items() if v > 0]
        if not candidates:
            break
        k = min(random.randint(pick_min, pick_max), len(candidates))
        pick = random.sample(candidates, k)
        for phone in pick:
            cli = clients.get(phone)
            if not cli:
                remaining_adds[phone] = 0
                continue
            member = await state.pop_member()
            try:
                if not member:
                    await manager.log("adder", f"No member to add - {phone}")
                else:
                    await _pace_before_action(phone, "sequence_add", min_delay, max_delay)
                    await cli.add_chat_members(add_ids[phone], member)
                    state.ok_count += 1
                    record_member_added(member, phone, state.current_task_id)
                    if use_remote_db and hasattr(state, '_remote_map') and member in state._remote_map:
                         mark_member_used_remote(state._remote_map[member])
                    await manager.log("adder", f"✅ {member} - {phone}")
            except (errors.SessionExpired, errors.SessionRevoked,
                    errors.UserDeactivatedBan, errors.UserDeactivated) as e:
                if member:
                    await state.return_member(member)
                _move_to_delete(phone)
                await manager.log("adder", f"❌ Banned: {e} - {phone}")
                remaining_adds[phone] = 0
            except errors.UserPrivacyRestricted:
                state.bad_count += 1
                await manager.log("adder", f"⛔ Privacy: {member}")
            except errors.FloodWait as e:
                if member:
                    await state.return_member(member)
                w = int(e.value)
                await manager.log("adder", f"⏳ Flood {w}s - {phone}")
                if w < flood_limit:
                    await asyncio.sleep(w + random.randint(5, 25))
            except errors.PeerFlood:
                if member:
                    await state.return_member(member)
                await manager.log("adder", f"🚫 PeerFlood - {phone}")
                remaining_adds[phone] = 0
            except errors.ChatMemberAddFailed:
                state.bad_count += 1
            except errors.UserChannelsTooMuch:
                state.bad_count += 1
            except Exception as e:
                state.bad_count += 1
                await manager.log("adder", f"⚠️ {e} - {phone}")
            remaining_adds[phone] -= 1

    if keep_online:
        await start_keepalive(clients)
    else:
        for phone, cli in clients.items():
            await TelegramPanel._safe_disconnect(cli, phone)

async def adder_account(phone, link, number_add, use_remote_db: bool = False):
    if not state.status:
        return

    cli = None
    try:
        min_delay = int(get_setting("min_delay", "20") or 20)
        max_delay = int(get_setting("max_delay", "100") or 100)
        flood_limit = int(get_setting("flood_wait_limit", "500") or 500)
        max_errors = int(get_setting("max_errors", "5") or 5)
        max_members_limit = int(get_setting("max_members_limit", "200") or 200)
        await manager.log("adder", f"Starting: {phone}")
        data = TelegramPanel.get_json_data(phone)
        proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
        cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)

        await asyncio.wait_for(cli.connect(), 15)
        await manager.log("adder", f"Connected: {phone}")

        chat = await TelegramPanel.join_chat(cli, int(link))
        if not chat["ok"]:
            await manager.log("adder", f"Join failed: {chat['error']} - {phone}")
            return

        await manager.log("adder", f"Joined: {chat['title']} - {phone}")
        await asyncio.sleep(1)

        info = await cli.get_chat(chat["id"])
        if info.members_count > max_members_limit:
            await manager.log("adder", f">{max_members_limit} members, stopping - {phone}")
            state.status = False
            return

        added, errs, ok, bad = 0, 0, 0, 0

        while state.status and added < number_add and errs <= max_errors:
            member = await state.pop_member()
            if not member:
                state.status = False
                await manager.log("adder", "No more members")
                break

            try:
                await _pace_before_action(phone, "add", min_delay, max_delay)
                await cli.add_chat_members(chat["id"], member)
                state.ok_count += 1
                added += 1
                ok += 1
                record_member_added(member, phone, state.current_task_id)
                if use_remote_db and hasattr(state, '_remote_map') and member in state._remote_map:
                     mark_member_used_remote(state._remote_map[member])
                await manager.log("adder", f"✅ {member} - {phone}")
                await manager.send_state()

            except (errors.SessionExpired, errors.SessionRevoked,
                    errors.UserDeactivatedBan, errors.UserDeactivated) as e:
                await state.return_member(member)
                _move_to_delete(phone)
                await manager.log("adder", f"❌ Banned: {e} - {phone}")
                return

            except errors.UserPrivacyRestricted:
                await manager.log("adder", f"⛔ Privacy: {member}")
                state.bad_count += 1; bad += 1

            except errors.FloodWait as e:
                await state.return_member(member)
                w = int(e.value)
                await manager.log("adder", f"⏳ Flood {w}s - {phone}")
                if w < flood_limit:
                    await asyncio.sleep(w + random.randint(5, 25))
                else:
                    break

            except errors.PeerFlood:
                await state.return_member(member)
                await manager.log("adder", f"🚫 PeerFlood - {phone}")
                break

            except errors.ChatMemberAddFailed:
                state.bad_count += 1; bad += 1

            except errors.UserChannelsTooMuch:
                state.bad_count += 1; bad += 1

            except Exception as e:
                state.bad_count += 1; bad += 1; errs += 1
                await manager.log("adder", f"⚠️ {e} - {phone}")

        await manager.log("adder", f"Done {phone}: ✅{ok} ⛔{bad}")

    except Exception as e:
        traceback.print_exc()
        await manager.log("adder", f"Error: {e} - {phone}")
    finally:
        if cli:
            await TelegramPanel._safe_disconnect(cli, phone)

async def run_adder(link, number_add, number_account, use_remote_db: bool = False):
    try:
        accs = TelegramPanel.list_accounts()
        selected = random.sample(accs, min(number_account, len(accs)))
        sem = asyncio.Semaphore(state.max_concurrent)
        tasks = []

        if use_remote_db:
             # Load from remote DB1
            rows = get_members_remote(limit=number_account * number_add * 2)
            state.members = [r["username"] for r in rows if r["username"]]
            state._remote_map = {r["username"]: r["user_id"] for r in rows if r["username"]}

        async def limited(phone):
            async with sem:
                try:
                    state.runs.append(phone)
                    await manager.send_state()
                    await adder_account(phone, link, number_add, use_remote_db)
                finally:
                    if phone in state.runs:
                        state.runs.remove(phone)
                    if phone not in state.final:
                        state.final.append(phone)
                    await manager.send_state()

        for phone in selected:
            tasks.append(asyncio.create_task(limited(phone)))
            await manager.log("adder", f"Queued: {phone}")

        await asyncio.gather(*tasks)
        await manager.log("adder", "All done")
        state.status = False
        await manager.send_state()

    except Exception as e:
        traceback.print_exc()
        await manager.log("adder", f"Fatal: {e}")
        state.status = False
        await manager.send_state()

async def scrape_process(link: str, limit: int, keywords_blacklist: list[str], min_length: int, save_to_remote: bool):
    cli = None
    phone = None
    try:
        phone = random.choice(TelegramPanel.list_accounts())
        await manager.log("scrape", f"Account: {phone}")

        data = TelegramPanel.get_json_data(phone)
        proxy, _ = await TelegramPanel.get_proxy(account_id=phone, ip=data.get("proxy"))
        cli = Client(f"account/{phone}", data["api_id"], data["api_hash"], proxy=proxy)

        await asyncio.wait_for(cli.connect(), 15)
        
        # Join or get chat
        chat = await TelegramPanel.join_chat(cli, link)
        if not chat["ok"]:
             await manager.log("scrape", f"Join failed: {chat['error']}")
             return
        
        count = 0
        async for msg in cli.get_chat_history(chat["id"], limit=limit):
            if not state.status: break
            if msg.text or msg.caption:
                text = msg.text or msg.caption
                if len(text) < min_length: continue
                if any(k in text for k in keywords_blacklist): continue
                
                if save_to_remote:
                    if save_chat_remote(text, link):
                        count += 1
                        await manager.log("scrape", f"Saved: {text[:20]}...")
                else:
                    await manager.log("scrape", f"Fetched: {text[:20]}...")
        
        await manager.log("scrape", f"Done. Saved {count} messages.")

    except Exception as e:
        traceback.print_exc()
        await manager.log("scrape", f"Error: {e}")
    finally:
        await TelegramPanel._safe_disconnect(cli, phone)
        state.status = False
        await manager.send_state()

async def task_loop():
    await asyncio.sleep(2)
    while True:
        try:
            if state.status or state.extract or state.chat_active:
                await asyncio.sleep(2)
                continue
            task = get_due_task()
            if not task:
                await asyncio.sleep(2)
                continue
            task_id = task["id"]
            task_type = task["type"]
            payload = json.loads(task["payload"])
            set_task_running(task_id)
            state.current_task_id = task_id
            state.current_task_type = task_type
            await manager.send_state()
            if state.keepalive:
                await stop_keepalive()
            if task_type == "extract":
                link = payload.get("link", "")
                if not link:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    state.extract = True
                    include_keywords = _normalize_keywords(payload.get("include_keywords", []))
                    exclude_keywords = _normalize_keywords(payload.get("exclude_keywords", []))
                    auto_load = bool(payload.get("auto_load", False))
                    use_remote_db = bool(payload.get("use_remote_db", False))
                    await extract_process(link, include_keywords, exclude_keywords, auto_load, None, True, use_remote_db)
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "extract_batch":
                links = _normalize_links(payload.get("links", []))
                if not links:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    state.extract = True
                    include_keywords = _normalize_keywords(payload.get("include_keywords", []))
                    exclude_keywords = _normalize_keywords(payload.get("exclude_keywords", []))
                    auto_load = bool(payload.get("auto_load", False))
                    use_remote_db = bool(payload.get("use_remote_db", False))
                    await extract_batch_process(links, include_keywords, exclude_keywords, auto_load, use_remote_db)
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "adder":
                link = payload.get("link", "")
                group_name = payload.get("group_name", "")
                number_add = int(payload.get("number_add", 1))
                number_account = int(payload.get("number_account", 1))
                use_remote_db = bool(payload.get("use_remote_db", False))
                state.status = True
                state.reset_adder()
                state.members = []
                if group_name:
                    await load_members_from_group(group_name)
                await run_adder(link, number_add, number_account, use_remote_db)
                update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "dm":
                group_name = payload.get("group_name", "")
                messages = payload.get("messages", [])
                number_account = int(payload.get("number_account", 1))
                min_delay = int(payload.get("min_delay", 10))
                max_delay = int(payload.get("max_delay", 30))
                accs = TelegramPanel.list_accounts()
                number_account = min(number_account, len(accs))
                state.status = True
                selected = random.sample(accs, number_account) if number_account < len(accs) else list(accs)
                await dm_process(group_name, selected, messages, min_delay, max_delay)
                state.status = False
                update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "warmup":
                number_account = int(payload.get("number_account", 1))
                duration_min = int(payload.get("duration_min", 10))
                actions = payload.get("actions", ["read", "scroll"])
                accs = TelegramPanel.list_accounts()
                number_account = min(number_account, len(accs))
                state.status = True
                selected = random.sample(accs, number_account) if number_account < len(accs) else list(accs)
                await warmup_process(selected, duration_min, actions)
                state.status = False
                update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "invite":
                link = payload.get("link", "")
                group_names = payload.get("group_names", [])
                number_add = int(payload.get("number_add", 1))
                number_account = int(payload.get("number_account", 1))
                use_remote_db = bool(payload.get("use_remote_db", False))
                if not link or number_add < 1 or number_account < 1:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    state.status = True
                    state.reset_adder()
                    state.members = []
                    if group_names:
                        await load_members_from_groups(group_names)
                    await run_adder(link, number_add, number_account, use_remote_db)
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "join":
                links = _normalize_links(payload.get("links", []))
                number_account = int(payload.get("number_account", 0))
                accs = TelegramPanel.list_accounts()
                if not links or not accs:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    number_account = min(number_account or len(accs), len(accs))
                    selected = random.sample(accs, number_account) if number_account < len(accs) else list(accs)
                    await join_groups_process(links, selected)
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "chat":
                link = payload.get("link", "")
                messages = payload.get("messages", [])
                number_account = int(payload.get("number_account", 1))
                min_delay = int(payload.get("min_delay", 10))
                max_delay = int(payload.get("max_delay", 30))
                max_messages = int(payload.get("max_messages", 50))
                use_remote_db = bool(payload.get("use_remote_db", False))
                accs = TelegramPanel.list_accounts()
                if not link or (not messages and not use_remote_db) or not accs:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    state.chat_active = True
                    number_account = min(number_account, len(accs))
                    selected = random.sample(accs, number_account) if number_account < len(accs) else list(accs)
                    await chat_process(link, selected, messages, min_delay, max_delay, max_messages, use_remote_db)
                    state.chat_active = False
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "sequence":
                chat_link = payload.get("chat_link", "")
                messages = payload.get("messages", [])
                min_delay = int(payload.get("min_delay", 10))
                max_delay = int(payload.get("max_delay", 30))
                chat_per_account = int(payload.get("chat_per_account", 10))
                pick_min = int(payload.get("pick_min", 1))
                pick_max = int(payload.get("pick_max", 3))
                add_link = payload.get("add_link", "")
                group_names = payload.get("group_names", [])
                use_loaded = bool(payload.get("use_loaded", True))
                adds_per_account = int(payload.get("adds_per_account", 10))
                number_account = int(payload.get("number_account", 100))
                keep_online = bool(payload.get("keep_online", True))
                use_remote_db = bool(payload.get("use_remote_db", False))
                use_remote_content = bool(payload.get("use_remote_content", False))
                
                if not chat_link or (not messages and not use_remote_content) or not add_link:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    state.status = True
                    await sequence_process(chat_link, messages, min_delay, max_delay, chat_per_account, pick_min, pick_max,
                                           add_link, group_names, use_loaded, adds_per_account, number_account, keep_online, use_remote_db, use_remote_content)
                    state.status = False
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            elif task_type == "scrape":
                link = payload.get("link", "")
                limit = int(payload.get("limit", 100))
                keywords_blacklist = _normalize_keywords(payload.get("keywords_blacklist", []))
                min_length = int(payload.get("min_length", 1))
                save_to_remote = bool(payload.get("save_to_remote", True))
                if not link:
                    update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
                else:
                    state.status = True
                    await scrape_process(link, limit, keywords_blacklist, min_length, save_to_remote)
                    state.status = False
                    update_task_status(task_id, "done", finished_at=int(datetime.utcnow().timestamp()))
            else:
                update_task_status(task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
            state.current_task_id = None
            state.current_task_type = None
            await manager.send_state()
        except Exception as e:
            if state.current_task_id:
                append_task_log(state.current_task_id, f"Fatal: {e}")
                update_task_status(state.current_task_id, "failed", finished_at=int(datetime.utcnow().timestamp()))
            state.status = False
            state.extract = False
            state.current_task_id = None
            state.current_task_type = None
            await manager.send_state()
        await asyncio.sleep(2)
