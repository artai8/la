"""代理管理器 - 解析/检测/Xray进程管理"""
import asyncio
import base64
import json
import logging
import os
import re
import signal
import subprocess
import urllib.parse
from typing import Optional

import httpx

from app.config import XRAY_BIN_PATH, XRAY_CONFIG_DIR, PROXY_BASE_PORT

logger = logging.getLogger(__name__)

# 全局: proxy_id -> (进程, 本地端口)
_xray_processes: dict[str, tuple[subprocess.Popen, int]] = {}
_next_port = PROXY_BASE_PORT
_port_lock = asyncio.Lock()


def _allocate_port() -> int:
    """分配下一个可用本地端口（同步版本，需在锁内调用或单线程上下文）"""
    global _next_port
    port = _next_port
    _next_port += 1
    return port


async def allocate_port_safe() -> int:
    """线程安全地分配端口"""
    async with _port_lock:
        return _allocate_port()


# ===================== 链接解析 =====================

def parse_proxy_link(link: str) -> dict:
    """解析 vless:// vmess:// trojan:// 链接为配置字典"""
    link = link.strip()
    if link.startswith("vless://"):
        return _parse_vless(link)
    elif link.startswith("vmess://"):
        return _parse_vmess(link)
    elif link.startswith("trojan://"):
        return _parse_trojan(link)
    else:
        raise ValueError(f"不支持的协议链接: {link[:30]}...")


def _parse_vless(link: str) -> dict:
    """解析 vless://uuid@host:port?params#remark"""
    # vless://uuid@host:port?type=tcp&security=tls&...#remark
    without_scheme = link[len("vless://"):]
    # 分离 fragment (remark)
    remark = ""
    if "#" in without_scheme:
        without_scheme, remark = without_scheme.rsplit("#", 1)
        remark = urllib.parse.unquote(remark)

    # 分离 query
    params_str = ""
    if "?" in without_scheme:
        without_scheme, params_str = without_scheme.split("?", 1)

    params = dict(urllib.parse.parse_qsl(params_str))

    # uuid@host:port
    uuid_part, host_port = without_scheme.split("@", 1)
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 443

    return {
        "protocol": "vless",
        "address": host,
        "port": port,
        "raw_link": link,
        "config_json": {
            "uuid": uuid_part,
            "encryption": params.get("encryption", "none"),
            "flow": params.get("flow", ""),
            "network": params.get("type", "tcp"),
            "security": params.get("security", "tls"),
            "sni": params.get("sni", host),
            "alpn": params.get("alpn", ""),
            "fp": params.get("fp", ""),
            "pbk": params.get("pbk", ""),
            "sid": params.get("sid", ""),
            "path": params.get("path", ""),
            "host": params.get("host", ""),
            "serviceName": params.get("serviceName", ""),
            "remark": remark,
        },
    }


def _parse_vmess(link: str) -> dict:
    """解析 vmess:// Base64编码的JSON"""
    encoded = link[len("vmess://"):]
    # 修正 base64 padding
    encoded += "=" * (4 - len(encoded) % 4) if len(encoded) % 4 else ""
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        cfg = json.loads(decoded)
    except Exception:
        raise ValueError("vmess 链接解码失败")

    return {
        "protocol": "vmess",
        "address": cfg.get("add", ""),
        "port": int(cfg.get("port", 443)),
        "raw_link": link,
        "config_json": {
            "uuid": cfg.get("id", ""),
            "alter_id": int(cfg.get("aid", 0)),
            "security": cfg.get("scy", "auto"),
            "network": cfg.get("net", "tcp"),
            "type": cfg.get("type", "none"),
            "host": cfg.get("host", ""),
            "path": cfg.get("path", ""),
            "tls": cfg.get("tls", ""),
            "sni": cfg.get("sni", ""),
            "alpn": cfg.get("alpn", ""),
            "fp": cfg.get("fp", ""),
            "remark": cfg.get("ps", ""),
        },
    }


def _parse_trojan(link: str) -> dict:
    """解析 trojan://password@host:port?params#remark"""
    without_scheme = link[len("trojan://"):]
    remark = ""
    if "#" in without_scheme:
        without_scheme, remark = without_scheme.rsplit("#", 1)
        remark = urllib.parse.unquote(remark)

    params_str = ""
    if "?" in without_scheme:
        without_scheme, params_str = without_scheme.split("?", 1)

    params = dict(urllib.parse.parse_qsl(params_str))

    password, host_port = without_scheme.split("@", 1)
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 443

    return {
        "protocol": "trojan",
        "address": host,
        "port": port,
        "raw_link": link,
        "config_json": {
            "password": password,
            "network": params.get("type", "tcp"),
            "security": params.get("security", "tls"),
            "sni": params.get("sni", host),
            "alpn": params.get("alpn", ""),
            "fp": params.get("fp", ""),
            "path": params.get("path", ""),
            "host": params.get("host", ""),
            "remark": remark,
        },
    }


async def parse_subscription(url: str) -> list[dict]:
    """解析订阅链接, 返回代理配置列表"""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content = resp.text

    # 尝试 Base64 解码
    try:
        decoded = base64.b64decode(content).decode("utf-8")
        lines = decoded.strip().splitlines()
    except Exception:
        lines = content.strip().splitlines()

    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = parse_proxy_link(line)
            results.append(parsed)
        except Exception as e:
            logger.warning(f"跳过无法解析的行: {line[:50]}... 错误: {e}")
    return results


# ===================== Xray 配置生成 =====================

def _generate_xray_config(proxy_data: dict, local_port: int) -> dict:
    """根据代理配置生成 xray-core JSON 配置"""
    protocol = proxy_data["protocol"]
    cfg = proxy_data.get("config_json", {})
    address = proxy_data["address"]
    port = proxy_data["port"]

    config = {
        "inbounds": [
            {
                "port": local_port,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            }
        ],
        "outbounds": [],
    }

    if protocol == "vless":
        outbound = {
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": address,
                        "port": port,
                        "users": [
                            {
                                "id": cfg.get("uuid", ""),
                                "encryption": cfg.get("encryption", "none"),
                                "flow": cfg.get("flow", ""),
                            }
                        ],
                    }
                ]
            },
            "streamSettings": _build_stream_settings(cfg),
        }
        config["outbounds"].append(outbound)

    elif protocol == "vmess":
        outbound = {
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": address,
                        "port": port,
                        "users": [
                            {
                                "id": cfg.get("uuid", ""),
                                "alterId": cfg.get("alter_id", 0),
                                "security": cfg.get("security", "auto"),
                            }
                        ],
                    }
                ]
            },
            "streamSettings": _build_stream_settings(cfg),
        }
        config["outbounds"].append(outbound)

    elif protocol == "trojan":
        outbound = {
            "protocol": "trojan",
            "settings": {
                "servers": [
                    {
                        "address": address,
                        "port": port,
                        "password": cfg.get("password", ""),
                    }
                ]
            },
            "streamSettings": _build_stream_settings(cfg),
        }
        config["outbounds"].append(outbound)

    return config


def _build_stream_settings(cfg: dict) -> dict:
    """构建 streamSettings"""
    network = cfg.get("network", "tcp")
    security = cfg.get("security", "tls")

    stream = {"network": network}

    # TLS / Reality settings
    if security == "tls":
        tls_settings = {"serverName": cfg.get("sni", "")}
        alpn = cfg.get("alpn", "")
        if alpn:
            tls_settings["alpn"] = alpn.split(",")
        fp = cfg.get("fp", "")
        if fp:
            tls_settings["fingerprint"] = fp
        stream["security"] = "tls"
        stream["tlsSettings"] = tls_settings
    elif security == "reality":
        reality_settings = {
            "serverName": cfg.get("sni", ""),
            "publicKey": cfg.get("pbk", ""),
            "shortId": cfg.get("sid", ""),
        }
        fp = cfg.get("fp", "")
        if fp:
            reality_settings["fingerprint"] = fp
        stream["security"] = "reality"
        stream["realitySettings"] = reality_settings
    else:
        stream["security"] = "none"

    # Network-specific settings
    if network == "ws":
        ws = {"path": cfg.get("path", "/")}
        host = cfg.get("host", "")
        if host:
            ws["headers"] = {"Host": host}
        stream["wsSettings"] = ws
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": cfg.get("serviceName", "")}
    elif network == "tcp":
        tcp_type = cfg.get("type", "none")
        if tcp_type == "http":
            stream["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "path": [cfg.get("path", "/")],
                        "headers": {"Host": [cfg.get("host", "")]},
                    },
                }
            }

    return stream


# ===================== Xray 进程管理 =====================

def start_xray_process(proxy_id: str, proxy_data: dict, local_port: int) -> bool:
    """启动 xray-core 进程"""
    global _xray_processes

    if proxy_id in _xray_processes:
        logger.warning(f"代理 {proxy_id} 的 xray 进程已在运行")
        return True

    # 生成配置文件
    config = _generate_xray_config(proxy_data, local_port)
    config_path = str(XRAY_CONFIG_DIR / f"{proxy_id}.json")

    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"写入 xray 配置失败: {e}")
        return False

    # 检查 xray 二进制是否存在
    if not os.path.isfile(XRAY_BIN_PATH):
        logger.error(f"xray 二进制不存在: {XRAY_BIN_PATH}")
        return False

    try:
        process = subprocess.Popen(
            [XRAY_BIN_PATH, "run", "-config", config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _xray_processes[proxy_id] = (process, local_port)
        logger.info(f"xray 进程启动成功: proxy={proxy_id}, port={local_port}, pid={process.pid}")
        return True
    except Exception as e:
        logger.error(f"启动 xray 进程失败: {e}")
        return False


def stop_xray_process(proxy_id: str):
    """停止 xray-core 进程"""
    global _xray_processes
    if proxy_id in _xray_processes:
        process, port = _xray_processes[proxy_id]
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        except Exception:
            pass
        del _xray_processes[proxy_id]
        # 清理配置文件
        config_path = XRAY_CONFIG_DIR / f"{proxy_id}.json"
        if config_path.exists():
            config_path.unlink()
        logger.info(f"xray 进程已停止: proxy={proxy_id}, port={port}")


def stop_all_xray():
    """停止所有 xray 进程"""
    for proxy_id in list(_xray_processes.keys()):
        stop_xray_process(proxy_id)


def get_proxy_socks_port(proxy_id: str) -> int | None:
    """获取代理对应的本地 SOCKS5 端口"""
    if proxy_id in _xray_processes:
        return _xray_processes[proxy_id][1]
    return None


def ensure_proxy_running(proxy_id: str, proxy_data: dict) -> int | None:
    """确保代理进程正在运行, 不在则启动, 返回本地 SOCKS5 端口

    用于 session_restorer 等场景, 按需启动代理。
    """
    # 已在运行
    port = get_proxy_socks_port(proxy_id)
    if port:
        # 检查进程是否还活着
        proc, p = _xray_processes[proxy_id]
        if proc.poll() is None:
            return port
        # 进程已退出, 清理并重新启动
        del _xray_processes[proxy_id]
        logger.warning(f"xray 进程已退出, 重新启动: proxy={proxy_id}")

    # 分配端口并启动
    new_port = _allocate_port()
    ok = start_xray_process(proxy_id, proxy_data, new_port)
    if ok:
        return new_port
    return None


# ===================== 代理检测 =====================

async def check_proxy(proxy_data: dict, local_port: int, timeout: int = 10) -> bool:
    """检测代理是否可用: 通过本地 SOCKS5 代理访问 https://t.me"""
    proxy_id = f"check_{local_port}"
    config = _generate_xray_config(proxy_data, local_port)
    config_path = str(XRAY_CONFIG_DIR / f"{proxy_id}.json")

    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"检测代理: 写入配置失败: {e}")
        return False

    if not os.path.isfile(XRAY_BIN_PATH):
        logger.warning(f"xray 不存在 ({XRAY_BIN_PATH}), 尝试直接HTTP检测")
        # Fallback: 如果没有 xray，无法检测 vless/vmess/trojan
        return False

    process = None
    try:
        process = subprocess.Popen(
            [XRAY_BIN_PATH, "run", "-config", config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # 等待 xray 启动
        await asyncio.sleep(2)

        # 通过 SOCKS5 代理发送 HTTP 请求
        async with httpx.AsyncClient(
            proxy=f"socks5://127.0.0.1:{local_port}",
            timeout=timeout,
        ) as client:
            resp = await client.get("https://t.me/")
            return resp.status_code == 200
    except Exception as e:
        logger.debug(f"代理检测失败 (port={local_port}): {e}")
        return False
    finally:
        if process:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                if process:
                    process.kill()
        config_file = XRAY_CONFIG_DIR / f"{proxy_id}.json"
        if config_file.exists():
            config_file.unlink()


async def batch_check_proxies(proxies: list[dict], concurrency: int = 5) -> dict[str, bool]:
    """批量并发检测代理, 返回 {proxy_id: is_alive}"""
    results = {}
    check_port = PROXY_BASE_PORT + 5000  # 使用高端口避免冲突
    semaphore = asyncio.Semaphore(concurrency)

    async def _check_one(proxy: dict, port: int):
        proxy_id = proxy.get("id", "")
        async with semaphore:
            is_alive = await check_proxy(
                {
                    "protocol": proxy.get("protocol", ""),
                    "address": proxy.get("address", ""),
                    "port": proxy.get("port", 0),
                    "config_json": proxy.get("config_json", {}),
                },
                port,
            )
            results[proxy_id] = is_alive
            logger.info(
                f"代理检测: {proxy_id} ({proxy.get('address', '')}:{proxy.get('port', '')}) "
                f"-> {'存活' if is_alive else '失效'}"
            )

    tasks = []
    for i, proxy in enumerate(proxies):
        port = check_port + i
        tasks.append(_check_one(proxy, port))

    await asyncio.gather(*tasks, return_exceptions=True)
    return results
