import pymysql
from app.core.database import get_setting

def _get_remote_conn():
    host = get_setting("db1_host")
    port = get_setting("db1_port")
    user = get_setting("db1_user")
    password = get_setting("db1_pass")
    db_name = get_setting("db1_name")
    
    if not host or not user or not db_name:
        return None
    
    try:
        return pymysql.connect(
            host=host,
            port=int(port) if port else 3306,
            user=user,
            password=password,
            database=db_name,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5
        )
    except Exception as e:
        print(f"MySQL connect error: {e}")
        return None

def insert_members(members: list[dict]):
    conn = _get_remote_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            # Assume table structure: members(username, user_id, access_hash, group_name, ...)
            # Or whatever the schema is. Based on context, it's for extraction.
            # "insert ignore into members ..."
            sql = """INSERT IGNORE INTO members (username, user_id, access_hash, group_id, group_title, status) 
                     VALUES (%s, %s, %s, %s, %s, 'active')"""
            data = []
            for m in members:
                data.append((
                    m.get("username"),
                    m.get("id"),
                    m.get("access_hash"),
                    m.get("group_id"),
                    m.get("group_title")
                ))
            cursor.executemany(sql, data)
        conn.commit()
    except Exception as e:
        print(f"MySQL insert error: {e}")
    finally:
        conn.close()
