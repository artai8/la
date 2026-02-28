import os
import sqlite3
import hashlib
import secrets
import time
import json
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = "data/panel.db"

def _db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _now_ts():
    return int(time.time())

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = _db()
    cur = conn.cursor()
    cur.execute("create table if not exists settings (key text primary key, value text)")
    cur.execute("""create table if not exists api_credentials (
        id integer primary key autoincrement,
        api_id integer not null,
        api_hash text not null,
        enabled integer default 1
    )""")
    cur.execute("""create table if not exists proxies (
        id integer primary key autoincrement,
        scheme text default 'socks5',
        host text not null,
        port integer not null,
        username text,
        password text,
        raw_url text,
        enabled integer default 1,
        last_check integer,
        ok integer
    )""")
    # Migration for existing table
    try:
        cur.execute("alter table proxies add column raw_url text")
    except sqlite3.OperationalError:
        pass
    cur.execute("""create table if not exists lists (
        id integer primary key autoincrement,
        list_type text not null,
        value text not null unique
    )""")
    cur.execute("""create table if not exists users (
        id integer primary key autoincrement,
        username text not null unique,
        password_hash text not null,
        salt text not null,
        role text not null,
        created_at integer not null
    )""")
    cur.execute("""create table if not exists sessions (
        token text primary key,
        user_id integer not null,
        expires_at integer not null
    )""")
    cur.execute("""create table if not exists tasks (
        id integer primary key autoincrement,
        type text not null,
        payload text not null,
        status text not null,
        run_at integer not null,
        created_at integer not null,
        started_at integer,
        finished_at integer,
        log text default ''
    )""")
    cur.execute("""create table if not exists members_added (
        id integer primary key autoincrement,
        username text not null,
        account text,
        task_id integer,
        created_at integer not null
    )""")
    cur.execute("""create table if not exists accounts (
        phone text primary key,
        status text,
        last_check integer,
        note text,
        group_name text default 'default',
        profile_updated_at integer,
        proxy_id integer
    )""")
    try:
        cur.execute("alter table accounts add column proxy_id integer")
    except sqlite3.OperationalError:
        pass
    cur.execute("""create table if not exists workers (
        id integer primary key autoincrement,
        name text not null unique,
        status text,
        last_ping integer
    )""")
    try:
        cur.execute("alter table workers add column last_ping integer")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def get_setting(key: str, default: str = "") -> str:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select value from settings where key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert or replace into settings(key, value) values(?,?)", (key, value))
    conn.commit()
    conn.close()

def list_api_credentials() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id, api_id, api_hash, enabled from api_credentials order by id desc")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def add_api_credential(api_id: int, api_hash: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert into api_credentials(api_id, api_hash, enabled) values(?,?,1)", (api_id, api_hash))
    conn.commit()
    conn.close()

def update_api_credential(row_id: int, api_id: int, api_hash: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("update api_credentials set api_id=?, api_hash=? where id=?", (api_id, api_hash, row_id))
    conn.commit()
    conn.close()

def set_api_enabled(row_id: int, enabled: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("update api_credentials set enabled=? where id=?", (enabled, row_id))
    conn.commit()
    conn.close()

def import_api_credentials(items: list[tuple[int, str]]):
    conn = _db()
    cur = conn.cursor()
    for api_id, api_hash in items:
        cur.execute("insert into api_credentials(api_id, api_hash, enabled) values(?,?,1)", (api_id, api_hash))
    conn.commit()
    conn.close()

def remove_api_credential(row_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from api_credentials where id=?", (row_id,))
    conn.commit()
    conn.close()

def list_proxies() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id, scheme, host, port, username, password, raw_url, enabled, last_check, ok from proxies order by id desc")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def add_proxy(scheme: str, host: str, port: int, username: str, password: str, raw_url: str = ""):
    conn = _db()
    cur = conn.cursor()
    cur.execute("""insert into proxies(scheme, host, port, username, password, raw_url, enabled)
                   values(?,?,?,?,?,?,1)""", (scheme, host, port, username, password, raw_url))
    conn.commit()
    conn.close()

def update_proxy(row_id: int, scheme: str, host: str, port: int, username: str, password: str, raw_url: str = ""):
    conn = _db()
    cur = conn.cursor()
    cur.execute("""update proxies set scheme=?, host=?, port=?, username=?, password=?, raw_url=? where id=?""",
                (scheme, host, port, username, password, raw_url, row_id))
    conn.commit()
    conn.close()

def set_proxy_enabled(row_id: int, enabled: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("update proxies set enabled=? where id=?", (enabled, row_id))
    conn.commit()
    conn.close()

def import_proxies(items: list[tuple[str, str, int, str, str, str]]):
    conn = _db()
    cur = conn.cursor()
    for scheme, host, port, username, password, raw_url in items:
        cur.execute("""insert into proxies(scheme, host, port, username, password, raw_url, enabled)
                       values(?,?,?,?,?,?,1)""", (scheme, host, port, username, password, raw_url))
    conn.commit()
    conn.close()

def remove_proxy(row_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from proxies where id=?", (row_id,))
    conn.commit()
    conn.close()

def update_proxy_check(row_id: int, ok: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("update proxies set last_check=?, ok=? where id=?", (_now_ts(), ok, row_id))
    conn.commit()
    conn.close()

def list_list_values(list_type: str) -> list[str]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select value from lists where list_type=?", (list_type,))
    rows = [r["value"] for r in cur.fetchall()]
    conn.close()
    return rows

def add_list_value(list_type: str, value: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert or ignore into lists(list_type, value) values(?,?)", (list_type, value))
    conn.commit()
    conn.close()

def remove_list_value(list_type: str, value: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from lists where list_type=? and value=?", (list_type, value))
    conn.commit()
    conn.close()

def list_users() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id, username, role, created_at from users order by id desc")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def create_user(username: str, password_hash: str, salt: str, role: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert into users(username, password_hash, salt, role, created_at) values(?,?,?,?,?)",
                (username, password_hash, salt, role, _now_ts()))
    conn.commit()
    conn.close()

def get_user_by_username(username: str) -> Optional[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select * from users where username=?", (username,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def remove_user(user_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from users where id=?", (user_id,))
    conn.commit()
    conn.close()

def create_session(user_id: int, expires_in: int = 86400 * 7) -> str:
    token = secrets.token_hex(32)
    expires_at = _now_ts() + expires_in
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert into sessions(token, user_id, expires_at) values(?,?,?)", (token, user_id, expires_at))
    conn.commit()
    conn.close()
    return token

def get_session(token: str) -> Optional[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select * from sessions where token=? and expires_at>?", (token, _now_ts()))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def revoke_session(token: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from sessions where token=?", (token,))
    conn.commit()
    conn.close()

def list_tasks(limit: int = 200) -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("""select id, type, payload, status, run_at, created_at, started_at, finished_at
                   from tasks order by id desc limit ?""", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def create_task(task_type: str, payload: dict, run_at: int) -> int:
    conn = _db()
    cur = conn.cursor()
    cur.execute("""insert into tasks(type, payload, status, run_at, created_at)
                   values(?,?,?,?,?)""", (task_type, json.dumps(payload), "queued", run_at, _now_ts()))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id

def update_task_status(task_id: int, status: str, started_at: int = None, finished_at: int = None):
    conn = _db()
    cur = conn.cursor()
    updates = ["status=?"]
    params = [status]
    if started_at:
        updates.append("started_at=?")
        params.append(started_at)
    if finished_at:
        updates.append("finished_at=?")
        params.append(finished_at)
    params.append(task_id)
    cur.execute(f"update tasks set {', '.join(updates)} where id=?", tuple(params))
    conn.commit()
    conn.close()

def set_task_running(task_id: int):
    update_task_status(task_id, "running", started_at=_now_ts())

def append_task_log(task_id: int, text: str):
    conn = _db()
    cur = conn.cursor()
    # append text with newline
    cur.execute("update tasks set log = log || ? where id=?", (text + "\n", task_id))
    conn.commit()
    conn.close()

def get_due_task() -> Optional[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("""select id, type, payload from tasks
                   where status='queued' and run_at<=?
                   order by run_at asc, id asc limit 1""", (_now_ts(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_task_log(task_id: int) -> str:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select log from tasks where id=?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return row["log"] if row and row["log"] else ""

def delete_task(task_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from tasks where id=?", (task_id,))
    conn.commit()
    conn.close()

def is_member_added(username: str) -> bool:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id from members_added where username=?", (username,))
    row = cur.fetchone()
    conn.close()
    return bool(row)

def record_member_added(username: str, account: str, task_id: Optional[int]):
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert into members_added(username, account, task_id, created_at) values(?,?,?,?)",
                (username, account, task_id, _now_ts()))
    conn.commit()
    conn.close()

def list_reports(start: int = None, end: int = None) -> dict:
    conn = _db()
    cur = conn.cursor()
    where = []
    params = []
    if start:
        where.append("created_at >= ?")
        params.append(start)
    if end:
        where.append("created_at <= ?")
        params.append(end)
    w_clause = "where " + " and ".join(where) if where else ""
    
    cur.execute(f"select count(*) as c from members_added {w_clause}", tuple(params))
    added = cur.fetchone()["c"]

    w_clause_task = "where " + " and ".join(where) if where else "" # tasks also use created_at or finished_at? let's use created_at
    cur.execute(f"select count(*) as c from tasks {w_clause_task} and status='done'", tuple(params))
    tasks_done = cur.fetchone()["c"]
    
    cur.execute(f"select count(*) as c from tasks {w_clause_task} and status='failed'", tuple(params))
    tasks_failed = cur.fetchone()["c"]
    
    conn.close()
    return {
        "added": added,
        "tasks_done": tasks_done,
        "tasks_failed": tasks_failed
    }

def list_workers() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select * from workers order by last_ping desc")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def upsert_worker(name: str, status: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert or replace into workers(name, status, last_ping) values(?,?,?)", (name, status, _now_ts()))
    conn.commit()
    conn.close()
