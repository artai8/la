import pymysql
import pymysql.cursors
from typing import Optional, List, Dict
from app.database import get_setting

def _get_conn(db_num: int):
    host = get_setting(f"db{db_num}_host")
    port = get_setting(f"db{db_num}_port")
    user = get_setting(f"db{db_num}_user")
    passwd = get_setting(f"db{db_num}_pass")
    db = get_setting(f"db{db_num}_name")

    if not host or not user or not db:
        return None

    try:
        conn = pymysql.connect(
            host=host,
            port=int(port) if port else 3306,
            user=user,
            password=passwd,
            database=db,
            cursorclass=pymysql.cursors.DictCursor
        )
        return conn
    except Exception as e:
        print(f"DB{db_num} connection error: {e}")
        return None

def init_db1_table():
    conn = _get_conn(1)
    if not conn: return
    try:
        with conn.cursor() as cursor:
            # table members: id, username, user_id, access_hash, group_source, status, created_at
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(255),
                    user_id BIGINT,
                    access_hash BIGINT,
                    group_source VARCHAR(255),
                    status VARCHAR(50) DEFAULT 'new',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_user (user_id)
                )
            """)
        conn.commit()
    except Exception as e:
        print(f"DB1 init error: {e}")
    finally:
        conn.close()

def init_db2_table():
    conn = _get_conn(2)
    if not conn: return
    try:
        with conn.cursor() as cursor:
            # table chat_content: id, content, source_group, created_at
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_content (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    content TEXT,
                    source_group VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()
    except Exception as e:
        print(f"DB2 init error: {e}")
    finally:
        conn.close()

# DB1 Helpers (Members)
def save_member_remote(username: str, user_id: int, access_hash: int, group_source: str):
    conn = _get_conn(1)
    if not conn: return False
    try:
        with conn.cursor() as cursor:
            sql = "INSERT IGNORE INTO members (username, user_id, access_hash, group_source) VALUES (%s, %s, %s, %s)"
            cursor.execute(sql, (username, user_id, access_hash, group_source))
        conn.commit()
        return True
    except Exception as e:
        print(f"DB1 save error: {e}")
        return False
    finally:
        conn.close()

def get_members_remote(limit: int = 100) -> List[Dict]:
    conn = _get_conn(1)
    if not conn: return []
    try:
        with conn.cursor() as cursor:
            sql = "SELECT * FROM members WHERE status='new' LIMIT %s"
            cursor.execute(sql, (limit,))
            return cursor.fetchall()
    except Exception:
        return []
    finally:
        conn.close()

def mark_member_used_remote(user_id: int, status: str = "invited"):
    conn = _get_conn(1)
    if not conn: return
    try:
        with conn.cursor() as cursor:
            sql = "UPDATE members SET status=%s WHERE user_id=%s"
            cursor.execute(sql, (status, user_id))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def is_member_invited_remote(user_id: int) -> bool:
    conn = _get_conn(1)
    if not conn: return False
    try:
        with conn.cursor() as cursor:
            sql = "SELECT id FROM members WHERE user_id=%s AND status != 'new'"
            cursor.execute(sql, (user_id,))
            return bool(cursor.fetchone())
    except Exception:
        return False
    finally:
        conn.close()

# DB2 Helpers (Content)
def save_chat_remote(content: str, source_group: str):
    conn = _get_conn(2)
    if not conn: return False
    try:
        with conn.cursor() as cursor:
            sql = "INSERT INTO chat_content (content, source_group) VALUES (%s, %s)"
            cursor.execute(sql, (content, source_group))
        conn.commit()
        return True
    except Exception as e:
        print(f"DB2 save error: {e}")
        return False
    finally:
        conn.close()

def get_chat_remote() -> Optional[str]:
    conn = _get_conn(2)
    if not conn: return None
    try:
        with conn.cursor() as cursor:
            # Get random content
            sql = "SELECT content FROM chat_content ORDER BY RAND() LIMIT 1"
            cursor.execute(sql)
            row = cursor.fetchone()
            return row["content"] if row else None
    except Exception:
        return None
    finally:
        conn.close()
