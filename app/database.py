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
        last_seen integer
    )""")
    conn.commit()
    conn.close()
    _ensure_default_settings()

def _ensure_default_settings():
    defaults = {
        "min_delay": "20",
        "max_delay": "100",
        "flood_wait_limit": "500",
        "max_errors": "5",
        "max_members_limit": "200",
        "max_concurrent": "0",
        "lang": "zh",
        "chat_interval_min": "15",
        "chat_interval_max": "45",
        "chat_messages": "hello\\nhi",
        "db1_host": "",
        "db1_port": "3306",
        "db1_user": "",
        "db1_pass": "",
        "db1_name": "",
        "db2_host": "",
        "db2_port": "3306",
        "db2_user": "",
        "db2_pass": "",
        "db2_name": "",
    }
    conn = _db()
    cur = conn.cursor()
    for k, v in defaults.items():
        cur.execute("insert or ignore into settings(key, value) values(?,?)", (k, v))
    conn.commit()
    conn.close()

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
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
    cur.execute("select value from lists where list_type=? order by id desc", (list_type,))
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

def is_member_added(username: str) -> bool:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select 1 from members_added where username=? limit 1", (username,))
    row = cur.fetchone()
    conn.close()
    return bool(row)

def record_member_added(username: str, account: str, task_id: Optional[int]):
    conn = _db()
    cur = conn.cursor()
    cur.execute("""insert into members_added(username, account, task_id, created_at)
                   values(?,?,?,?)""", (username, account, task_id, _now_ts()))
    conn.commit()
    conn.close()

def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()

def has_users() -> bool:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select 1 from users limit 1")
    row = cur.fetchone()
    conn.close()
    return bool(row)

def create_user(username: str, password: str, role: str):
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    conn = _db()
    cur = conn.cursor()
    cur.execute("""insert into users(username, password_hash, salt, role, created_at)
                   values(?,?,?,?,?)""", (username, password_hash, salt, role, _now_ts()))
    conn.commit()
    conn.close()

def list_users() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id, username, role, created_at from users order by id desc")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def remove_user(user_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("delete from users where id=?", (user_id,))
    conn.commit()
    conn.close()

def verify_user(username: str, password: str) -> Optional[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id, username, password_hash, salt, role from users where username=?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    if _hash_password(password, row["salt"]) != row["password_hash"]:
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}

def create_session(user_id: int, hours: int = 12) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = int((datetime.utcnow() + timedelta(hours=hours)).timestamp())
    conn = _db()
    cur = conn.cursor()
    cur.execute("insert into sessions(token, user_id, expires_at) values(?,?,?)", (token, user_id, expires_at))
    conn.commit()
    conn.close()
    return token

def get_user_by_session(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _db()
    cur = conn.cursor()
    cur.execute("""select u.id, u.username, u.role, s.expires_at
                   from sessions s join users u on s.user_id=u.id
                   where s.token=?""", (token,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    if row["expires_at"] < _now_ts():
        cur.execute("delete from sessions where token=?", (token,))
        conn.commit()
        conn.close()
        return None
    conn.close()
    return {"id": row["id"], "username": row["username"], "role": row["role"]}

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

def get_due_task() -> Optional[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("""select id, type, payload from tasks
                   where status='queued' and run_at<=?
                   order by run_at asc, id asc limit 1""", (_now_ts(),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def set_task_running(task_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("update tasks set status='running', started_at=? where id=?", (_now_ts(), task_id))
    conn.commit()
    conn.close()

def create_task(task_type: str, payload: dict, run_at: int) -> int:
    conn = _db()
    cur = conn.cursor()
    cur.execute("""insert into tasks(type, payload, status, run_at, created_at)
                   values(?,?,?,?,?)""", (task_type, json.dumps(payload), "queued", run_at, _now_ts()))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id

def update_task_status(task_id: int, status: str, started_at: Optional[int] = None, finished_at: Optional[int] = None):
    conn = _db()
    cur = conn.cursor()
    cur.execute("""update tasks set status=?, started_at=coalesce(?, started_at),
                   finished_at=coalesce(?, finished_at) where id=?""", (status, started_at, finished_at, task_id))
    conn.commit()
    conn.close()

def append_task_log(task_id: int, text: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("select log from tasks where id=?", (task_id,))
    row = cur.fetchone()
    current = row["log"] if row and row["log"] else ""
    current = (current + "\n" + text).strip()
    cur.execute("update tasks set log=? where id=?", (current, task_id))
    conn.commit()
    conn.close()

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

def get_task_payload(task_id: int) -> Optional[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select payload from tasks where id=?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return json.loads(row["payload"]) if row else None

def list_reports(start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> dict:
    conn = _db()
    cur = conn.cursor()
    added_sql = "select count(*) as cnt from members_added"
    added_params = []
    if start_ts is not None or end_ts is not None:
        clauses = []
        if start_ts is not None:
            clauses.append("created_at>=?")
            added_params.append(start_ts)
        if end_ts is not None:
            clauses.append("created_at<=?")
            added_params.append(end_ts)
        added_sql += " where " + " and ".join(clauses)
    cur.execute(added_sql, tuple(added_params))
    added = cur.fetchone()["cnt"]

    def _count_tasks(status: str) -> int:
        sql = "select count(*) as cnt from tasks where status=?"
        params = [status]
        if start_ts is not None or end_ts is not None:
            clauses = []
            if start_ts is not None:
                clauses.append("finished_at>=?")
                params.append(start_ts)
            if end_ts is not None:
                clauses.append("finished_at<=?")
                params.append(end_ts)
            sql += " and finished_at is not null and " + " and ".join(clauses)
        cur.execute(sql, tuple(params))
        return cur.fetchone()["cnt"]

    done = _count_tasks("done")
    failed = _count_tasks("failed")
    conn.close()
    return {"added": added, "tasks_done": done, "tasks_failed": failed}

def list_workers() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id, name, status, last_seen from workers order by id desc")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def upsert_worker(name: str, status: str):
    conn = _db()
    cur = conn.cursor()
    cur.execute("select id from workers where name=?", (name,))
    row = cur.fetchone()
    if row:
        cur.execute("update workers set status=?, last_seen=? where id=?", (status, _now_ts(), row["id"]))
    else:
        cur.execute("insert into workers(name, status, last_seen) values(?,?,?)", (name, status, _now_ts()))
    conn.commit()
    conn.close()
