"""Install a namespace firewall before starting any browser or proxy traffic."""

import os
from pathlib import Path
import signal
import subprocess
import sys

from config import ConfigError, load_config
from relay import RelayServer, probe_upstream


RELAY_UID = 10001


def command(*args, input=None):
    subprocess.run(args, input=input, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ipv6_disabled():
    try:
        return all(Path(f"/proc/sys/net/ipv6/conf/{scope}/disable_ipv6").read_text().strip() == "1" for scope in ("all", "default"))
    except OSError:
        return False


def lock_policies():
    # Set DROP first. Existing rules are replaced atomically later, never flushed
    # into an ACCEPT policy, including when a previous gateway has restarted.
    for chain in ("OUTPUT", "INPUT", "FORWARD"):
        command("iptables", "--wait", "5", "-P", chain, "DROP")
    try:
        for chain in ("OUTPUT", "INPUT", "FORWARD"):
            command("ip6tables", "--wait", "5", "-P", chain, "DROP")
        command("ip6tables-restore", "--wait", "5", input="*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\nCOMMIT\n")
    except subprocess.CalledProcessError:
        if not ipv6_disabled():
            raise RuntimeError("IPv6 could not be blocked") from None


def install_rules(config):
    # 127.0.0.11 is Docker's DNS forwarder. Reject it before the loopback rule
    # and before established flows, and allow only literal 127.0.0.1 loopback.
    rules = f"""*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT DROP [0:0]
-A INPUT -s 127.0.0.11/32 -j DROP
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A INPUT -i lo -s 127.0.0.1/32 -d 127.0.0.1/32 -j ACCEPT
-A INPUT -p tcp --dport 3000 -m conntrack --ctstate NEW -j ACCEPT
-A OUTPUT -d 127.0.0.11/32 -j DROP
-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A OUTPUT -o lo -s 127.0.0.1/32 -d 127.0.0.1/32 -j ACCEPT
-A OUTPUT -p tcp -d {config.server}/32 --dport {config.port} -m owner --uid-owner {RELAY_UID} -m conntrack --ctstate NEW -j ACCEPT
COMMIT
"""
    command("iptables-restore", "--wait", "5", input=rules)


def main():
    try:
        lock_policies()
        config = load_config(os.environ.get("PROXY_CONFIG_FILE", "/run/secrets/proxy.json"))
        install_rules(config)
        os.setgroups([])
        os.setgid(RELAY_UID)
        os.setuid(RELAY_UID)
        probe_upstream(config)
        with RelayServer(("127.0.0.1", 1080), config) as server:
            def terminate(signum, frame):
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, terminate)
            signal.signal(signal.SIGINT, terminate)
            print("SOCKS5 出口检查通过；网络隔离已启用。", flush=True)
            server.serve_forever(poll_interval=0.5)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception:
        print("代理网关未就绪：请检查节点配置、认证和连通性。已阻止直连。", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
