"""Strict configuration parsing shared by the launcher and gateway."""

from dataclasses import dataclass, field
import argparse
import ipaddress
import json
from pathlib import Path
import sys


class ConfigError(ValueError):
    """An actionable error that never includes user-provided secret values."""


@dataclass(frozen=True)
class ProxyConfig:
    server: str
    port: int
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    connect_timeout_seconds: int = 15


def load_config(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            value = json.load(stream)
    except OSError:
        raise ConfigError("无法读取代理配置文件。") from None
    except (UnicodeError, json.JSONDecodeError):
        raise ConfigError("代理配置必须是有效的 UTF-8 JSON。") from None
    if not isinstance(value, dict):
        raise ConfigError("代理配置的根节点必须是 JSON 对象。")
    allowed = {"server", "port", "username", "password", "connect_timeout_seconds"}
    if set(value) - allowed:
        raise ConfigError("代理配置包含不支持的字段，请对照示例检查字段名称。")
    server = value.get("server")
    try:
        if not isinstance(server, str):
            raise ValueError
        address = ipaddress.IPv4Address(server)
    except (ValueError, ipaddress.AddressValueError):
        raise ConfigError("server 必须填写 SOCKS5 节点的 IPv4 地址，不能留空或使用域名。") from None
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or address == ipaddress.IPv4Address("255.255.255.255"):
        raise ConfigError("server 不能使用回环、未指定、组播、链路本地或广播地址。")
    port = value.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("port 必须是 1–65535 的整数。")
    credentials = []
    for name in ("username", "password"):
        item = value.get(name, "")
        try:
            valid = isinstance(item, str) and len(item.encode("utf-8")) <= 255
        except UnicodeError:
            valid = False
        if not valid:
            raise ConfigError("username 和 password 必须是 UTF-8 长度不超过 255 字节的字符串。")
        credentials.append(item)
    username, password = credentials
    if bool(username) != bool(password):
        raise ConfigError("username 和 password 必须同时填写，或同时留空。")
    timeout = value.get("connect_timeout_seconds", 15)
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ConfigError("connect_timeout_seconds 必须是 1–60 的整数。")
    return ProxyConfig(str(address), port, username, password, timeout)


def main():
    parser = argparse.ArgumentParser(description="校验本地 SOCKS5 代理配置（不打印凭据）。")
    parser.add_argument("--check", required=True, metavar="FILE")
    args = parser.parse_args()
    try:
        load_config(args.check)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
