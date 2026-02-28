import os
import random
import asyncio
import aiohttp
import json
import re
import psutil
from typing import Optional, Tuple
from pyrogram import Client, errors, enums

from app.core.database import list_proxies, list_api_credentials, get_setting, _db
from app.core.v2ray import V2RayController

class TelegramPanel:
    # We'll store active v2ray ports here for cleanup
    active_v2ray_ports: dict[str, int] = {}

    @staticmethod
    async def get_proxy(account_id: str = None, proxy_id: int = None, ip: str = None) -> tuple[Optional[dict], bool]:
        """
        Retrieves a proxy. If it's a v2ray link, starts a local v2ray process.
        Returns (proxy_dict, is_v2ray).
        """
        # If we have an active v2ray port for this account, stop it first
        if account_id and account_id in TelegramPanel.active_v2ray_ports:
            V2RayController.stop(TelegramPanel.active_v2ray_ports[account_id])
            del TelegramPanel.active_v2ray_ports[account_id]

        proxies_db = [p for p in list_proxies() if p.get("enabled")]
        if not proxies_db:
            return None, False

        selected_proxy = None
        
        # 1. Try to find bound proxy for account
        if account_id and not proxy_id:
            conn = _db()
            cur = conn.cursor()
            cur.execute("select proxy_id from accounts where phone=?", (account_id,))
            row = cur.fetchone()
            conn.close()
            
            if row and row["proxy_id"]:
                # Check if this proxy is still valid/enabled
                bound = next((p for p in proxies_db if p["id"] == row["proxy_id"]), None)
                if bound:
                    selected_proxy = bound
                else:
                    # Bound proxy not found or disabled.
                    # "Unless this proxy fails" -> Pick new one?
                    pass

        # 2. If proxy_id specified (override), use it
        if not selected_proxy and proxy_id:
            selected_proxy = next((p for p in proxies_db if p["id"] == proxy_id), None)
            
        # 3. If still no proxy, pick random v2ray proxy and bind it
        if not selected_proxy:
             # Prefer v2ray links as user requested "delete socks5... because v2ray"
             v2ray_proxies = [p for p in proxies_db if p.get("raw_url")]
             if v2ray_proxies:
                 selected_proxy = random.choice(v2ray_proxies)
             else:
                 selected_proxy = random.choice(proxies_db)
            
             # Bind to account if account_id is present
             if account_id and selected_proxy:
                 conn = _db()
                 cur = conn.cursor()
                 cur.execute("update accounts set proxy_id=? where phone=?", (selected_proxy["id"], account_id))
                 conn.commit()
                 conn.close()

        if not selected_proxy:
            return None, False

        if selected_proxy.get("raw_url"):
            # It's a v2ray link
            loop = asyncio.get_running_loop()
            port, err = await loop.run_in_executor(None, V2RayController.start, selected_proxy["raw_url"])
            if err:
                print(f"V2Ray start error: {err}")
                # If start failed, maybe mark proxy as bad?
                return None, False
            
            if account_id:
                TelegramPanel.active_v2ray_ports[account_id] = port
            
            return {
                "scheme": "socks5",
                "hostname": "127.0.0.1",
                "port": port
            }, True
        else:
            # Normal static proxy (Should rarely happen if user deleted them, but support legacy)
            return {
                "scheme": selected_proxy.get("scheme", "socks5"),
                "hostname": selected_proxy["host"],
                "port": selected_proxy["port"],
                "username": selected_proxy.get("username"),
                "password": selected_proxy.get("password"),
            }, False

    @staticmethod
    async def _safe_disconnect(cli: Client, account_id: str = None):
        try:
            await cli.disconnect()
        except:
            pass
        if account_id and account_id in TelegramPanel.active_v2ray_ports:
            V2RayController.stop(TelegramPanel.active_v2ray_ports[account_id])
            del TelegramPanel.active_v2ray_ports[account_id]

    @staticmethod
    def list_accounts() -> list[str]:
        try:
            sessions = {
                f.name.replace(".session", "")
                for f in os.scandir("account")
                if f.name.endswith(".session")
            }
            jsons = {
                f.name.replace(".json", "")
                for f in os.scandir("data")
                if f.name.endswith(".json")
            }
            return sorted(sessions & jsons)
        except FileNotFoundError:
            return []

    @staticmethod
    def remove_account(phone: str) -> bool:
        for path in [f"account/{phone}.session", f"data/{phone}.json"]:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        return True

    @staticmethod
    def read_proxies_from_file() -> list[str]:
        try:
            with open("proxy.txt", "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
                return [l for l in lines if l.count(":") >= 3]
        except FileNotFoundError:
            return []

    @staticmethod
    def build_proxy(parts: list[str]) -> dict:
        return {
            "scheme": "socks5",
            "hostname": parts[0],
            "port": int(parts[1]),
            "username": parts[2],
            "password": parts[3],
        }

    @staticmethod
    async def check_proxy(ip, port, username, password, timeout=5) -> bool:
        if username and password:
            proxy_url = f"socks5://{username}:{password}@{ip}:{port}"
        else:
            proxy_url = f"socks5://{ip}:{port}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://core.telegram.org/bots",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def get_random_api() -> tuple[int, str]:
        rows = [r for r in list_api_credentials() if r.get("enabled")]
        valid_rows = []
        for row in rows:
            try:
                api_id = int(row["api_id"])
            except Exception:
                continue
            api_hash = row.get("api_hash") or ""
            if api_hash:
                valid_rows.append((api_id, api_hash))
        if valid_rows:
            return random.choice(valid_rows)
        try:
            with open("api.txt", "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if ":" in l and l.strip()]
        except FileNotFoundError:
            lines = []
        valid_lines = []
        for line in lines:
            api_id_str, api_hash = line.split(":", 1)
            api_id_str = api_id_str.strip()
            api_hash = api_hash.strip()
            try:
                api_id = int(api_id_str)
            except Exception:
                continue
            if api_hash:
                valid_lines.append((api_id, api_hash))
        if not valid_lines:
            raise ValueError("No valid API credentials found")
        return random.choice(valid_lines)

    @staticmethod
    async def add_account(phone: str) -> dict:
        if phone in TelegramPanel.list_accounts():
            return {"status": False, "message": f"Account {phone} already exists"}

        try:
            api_id, api_hash = TelegramPanel.get_random_api()
        except ValueError as e:
            return {"status": False, "message": str(e)}

        proxy, _ = await TelegramPanel.get_proxy(phone)
        cli = Client(f"account/{phone}", api_id, api_hash, proxy=proxy)

        try:
            await cli.connect()
            result = await cli.send_code(phone)
            return {
                "status": True,
                "cli": cli,
                "phone": phone,
                "code_hash": result.phone_code_hash,
                "api_id": api_id,
                "api_hash": api_hash,
                "proxy": proxy["hostname"] if proxy else "",
            }
        except Exception as e:
            await TelegramPanel._safe_disconnect(cli, phone)
            TelegramPanel._safe_remove_session(phone)
            return {"status": False, "message": str(e)}

    @staticmethod
    async def verify_code(cli, phone, code_hash, code) -> dict:
        try:
            await cli.sign_in(phone, code_hash, code)
            info = await cli.get_me()
            await TelegramPanel._safe_disconnect(cli, phone)
            return {"status": True, "message": f"Logged in: {phone} ({info.first_name})"}
        except errors.PhoneCodeInvalid:
            return {"status": False, "message": "invalid_code"}
        except errors.SessionPasswordNeeded:
            return {"status": False, "message": "FA2"}
        except Exception as e:
            await TelegramPanel._safe_disconnect(cli, phone)
            TelegramPanel._safe_remove_session(phone)
            return {"status": False, "message": str(e)}

    @staticmethod
    async def verify_password(cli, phone, password) -> dict:
        try:
            await cli.check_password(password=password)
            info = await cli.get_me()
            await TelegramPanel._safe_disconnect(cli, phone)
            return {"status": True, "message": f"Logged in: {phone} ({info.first_name})"}
        except errors.PasswordHashInvalid:
            return {"status": False, "message": "invalid_password"}
        except Exception as e:
            await TelegramPanel._safe_disconnect(cli, phone)
            TelegramPanel._safe_remove_session(phone)
            return {"status": False, "message": str(e)}

    @staticmethod
    async def cancel_account(cli, phone):
        await TelegramPanel._safe_disconnect(cli, phone)
        TelegramPanel._safe_remove_session(phone)

    @staticmethod
    def save_json(phone, data) -> bool:
        try:
            with open(f"data/{phone}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False

    @staticmethod
    def make_json_data(phone, api_id, api_hash, proxy, fa2) -> bool:
        return TelegramPanel.save_json(phone, {
            "api_id": api_id, "api_hash": api_hash,
            "proxy": proxy, "fa2": fa2,
        })

    @staticmethod
    def get_json_data(phone) -> Optional[dict]:
        try:
            with open(f"data/{phone}.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def list_groups() -> list[str]:
        try:
            return sorted([
                f.name.replace(".txt", "")
                for f in os.scandir("gaps")
                if f.name.endswith(".txt")
            ])
        except FileNotFoundError:
            return []

    @staticmethod
    def load_group(name) -> list[str]:
        try:
            with open(f"gaps/{name}.txt", "r", encoding="utf-8") as f:
                return [l.strip() for l in f if l.strip()]
        except Exception:
            return []

    @staticmethod
    def remove_group(name) -> bool:
        try:
            os.remove(f"gaps/{name}.txt")
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def is_valid_telegram_link(text) -> bool:
        p1 = r"^@[a-zA-Z0-9_]{5,}$"
        p2 = r"^t\.me/\+[\w\-]{10,}$"
        return bool(re.match(p1, text)) or bool(re.match(p2, text))

    @staticmethod
    def get_max_concurrent() -> int:
        ram_gb = psutil.virtual_memory().total / (1024**3)
        cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True)
        ram_gb = int(ram_gb + 0.5)
        for max_ram, max_cpu, result in [
            (2,2,3),(3,2,5),(4,4,6),(6,4,8),(8,6,10),(10,8,12)
        ]:
            if ram_gb <= max_ram and cpu_cores <= max_cpu:
                return result
        return 20

    @staticmethod
    async def join_chat(cli, link):
        try:
            if isinstance(link, str):
                if link.startswith("@") or "t.me" in link:
                    chat = await cli.join_chat(link)
                else:
                    try:
                        chat = await cli.get_chat(int(link))
                    except ValueError:
                        chat = await cli.join_chat(link)
            else:
                chat = await cli.get_chat(link)
            return {"ok": True, "id": chat.id, "title": chat.title, "link": link}
        except errors.bad_request_400.UserAlreadyParticipant:
            try:
                info = await cli.get_chat(link)
                return {"ok": True, "id": info.id, "title": info.title, "link": link}
            except Exception as e:
                 return {"ok": False, "error": f"UserAlreadyParticipant but fetch failed: {e}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    async def update_profile(cli, first_name=None, last_name=None, about=None, username=None):
        try:
            if first_name or last_name:
                await cli.update_profile(first_name=first_name, last_name=last_name)
            if about:
                await cli.update_profile(bio=about)
            if username:
                await cli.set_username(username)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    async def send_dm(cli, username, message):
        try:
            await cli.send_message(username, message)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    @staticmethod
    async def warmup_action(cli, action):
        try:
            if action == "read":
                dialogs = []
                async for d in cli.get_dialogs(limit=5):
                    dialogs.append(d)
                if dialogs:
                    target = random.choice(dialogs)
                    await cli.read_chat_history(target.chat.id)
                    return {"ok": True, "action": f"read {target.chat.title}"}
            elif action == "scroll":
                # Mock scrolling by getting history
                dialogs = []
                async for d in cli.get_dialogs(limit=3):
                    dialogs.append(d)
                if dialogs:
                    target = random.choice(dialogs)
                    async for msg in cli.get_chat_history(target.chat.id, limit=3):
                        pass
                    return {"ok": True, "action": f"scroll {target.chat.title}"}
            return {"ok": True, "action": "idle"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _safe_remove_session(phone):
        try:
            os.remove(f"account/{phone}.session")
        except FileNotFoundError:
            pass
