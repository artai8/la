import os
import json
import base64
import subprocess
import socket
import time
import uuid
import atexit
import signal
import platform
import urllib.parse
from typing import Optional, Tuple, Dict, Union

# Path to v2ray executable.
V2RAY_BIN = "v2ray"

class V2RayController:
    _instances: Dict[int, subprocess.Popen] = {}
    _config_files: Dict[int, str] = {}

    @staticmethod
    def stop(port: int):
        if port in V2RayController._instances:
            inst = V2RayController._instances[port]
            try:
                inst.terminate()
                inst.wait(timeout=2)
            except:
                try:
                    inst.kill()
                except:
                    pass
            
            del V2RayController._instances[port]
        
        if port in V2RayController._config_files:
            try:
                os.remove(V2RayController._config_files[port])
            except:
                pass
            del V2RayController._config_files[port]

    @staticmethod
    def stop_all():
        for port in list(V2RayController._instances.keys()):
            V2RayController.stop(port)

    @staticmethod
    def get_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    @staticmethod
    def _parse_vmess(url: str) -> dict:
        # vmess://base64_json
        try:
            b64 = url.replace("vmess://", "")
            # Fix padding
            padding = len(b64) % 4
            if padding:
                b64 += "=" * (4 - padding)
            conf = json.loads(base64.b64decode(b64).decode("utf-8"))
            
            return {
                "protocol": "vmess",
                "settings": {
                    "vnext": [{
                        "address": conf.get("add"),
                        "port": int(conf.get("port")),
                        "users": [{
                            "id": conf.get("id"),
                            "alterId": int(conf.get("aid", 0)),
                            "security": conf.get("scy", "auto"),
                            "level": 0
                        }]
                    }]
                },
                "streamSettings": {
                    "network": conf.get("net", "tcp"),
                    "security": conf.get("tls", ""),
                    "wsSettings": {
                        "path": conf.get("path", ""),
                        "headers": {
                            "Host": conf.get("host", "")
                        }
                    } if conf.get("net") == "ws" else None,
                    "tcpSettings": {
                         "header": {
                             "type": conf.get("type", "none")
                         }
                    } if conf.get("net") == "tcp" else None,
                    "tlsSettings": {
                        "serverName": conf.get("host", "") or conf.get("sni", "")
                    } if conf.get("tls") == "tls" else None
                }
            }
        except Exception as e:
            print(f"Error parsing vmess: {e}")
            return None

    @staticmethod
    def _parse_vless(url: str) -> dict:
        # vless://uuid@host:port?params#name
        try:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            
            uuid_val = parsed.username
            host = parsed.hostname
            port = parsed.port
            
            security = params.get("security", ["none"])[0]
            encryption = params.get("encryption", ["none"])[0]
            type_net = params.get("type", ["tcp"])[0]
            
            stream_settings = {
                "network": type_net,
                "security": security,
            }
            
            if security == "tls":
                stream_settings["tlsSettings"] = {
                    "serverName": params.get("sni", [host])[0] or host
                }
            
            if type_net == "ws":
                stream_settings["wsSettings"] = {
                    "path": params.get("path", ["/"])[0],
                    "headers": {
                        "Host": params.get("host", [host])[0]
                    }
                }
                
            return {
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": host,
                        "port": port,
                        "users": [{
                            "id": uuid_val,
                            "encryption": encryption,
                            "level": 0
                        }]
                    }]
                },
                "streamSettings": stream_settings
            }
        except Exception as e:
            print(f"Error parsing vless: {e}")
            return None

    @staticmethod
    def _generate_config(outbound: dict, local_port: int) -> dict:
        return {
            "log": {
                "loglevel": "warning"
            },
            "inbounds": [{
                "port": local_port,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": True
                }
            }],
            "outbounds": [
                outbound,
                {
                    "protocol": "freedom",
                    "tag": "direct",
                    "settings": {}
                }
            ]
        }

    @staticmethod
    def start(raw_url: str) -> Tuple[Optional[int], Optional[str]]:
        """
        Start a v2ray instance for the given raw_url.
        Returns (local_port, error_message).
        """
        outbound = None
        if raw_url.startswith("vmess://"):
            outbound = V2RayController._parse_vmess(raw_url)
        elif raw_url.startswith("vless://"):
            outbound = V2RayController._parse_vless(raw_url)
        # TODO: Add trojan/ss support if needed
        
        if not outbound:
            return None, "Unsupported or invalid protocol"

        local_port = V2RayController.get_free_port()
        config_data = V2RayController._generate_config(outbound, local_port)
        
        # Create temp config file
        config_dir = os.path.join("data", "v2ray_configs")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.abspath(os.path.join(config_dir, f"config_{local_port}_{uuid.uuid4().hex[:8]}.json"))
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        try:
            # Local run (Linux/Railway)
            cmd = [V2RAY_BIN, "run", "-c", config_path]
            proc = subprocess.Popen(cmd)
            
            V2RayController._instances[local_port] = proc
            V2RayController._config_files[local_port] = config_path
            time.sleep(1)
            
            if proc.poll() is not None:
                return None, f"V2Ray process exited immediately with code {proc.returncode}"
            
            return local_port, None

        except Exception as e:
            return None, str(e)

# Register cleanup
atexit.register(V2RayController.stop_all)
