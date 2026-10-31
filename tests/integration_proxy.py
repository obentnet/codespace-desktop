#!/usr/bin/env python3
"""Exercise the real gateway firewall using disposable Docker resources.

Run from anywhere with ``python3 tests/integration_proxy.py``. No production proxy
is used: a private mock node authenticates fake credentials, serves a test target,
and forwards only the gateway's example.com:443 readiness request to the internet.
The test publishes the control UI on a random localhost port and cleans only its
own containers, network and image tag. Set PROXY_TEST_REAL_CHROME=1 to also launch
the actual Chrome image and verify its managed proxy policy without CLI flags.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
CLIENT_IMAGE = os.environ.get("PROXY_TEST_CLIENT_IMAGE", "python:3.12-slim")
CHROME_IMAGE = os.environ.get("PROXY_TEST_CHROME_IMAGE", "lscr.io/linuxserver/chrome:latest")
REAL_CHROME = os.environ.get("PROXY_TEST_REAL_CHROME") == "1"
CONFIG_PATH = "/run/secrets/proxy.json"
TEST_USER = "integration-user"
TEST_PASSWORD = "integration-password-!@#-not-a-real-secret"


MOCK = r'''
import http.server, json, select, socket, socketserver, struct, threading

metrics = {"authenticated_connections": 0, "dns_queries": 0}

def exact(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count-len(data))
        if not part:
            raise EOFError()
        data += part
    return data

class Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (json.dumps(metrics).encode() if self.path == "/metrics"
                else b"via-test-node\n")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

class Socks(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        try:
            self.request.settimeout(10)
            ver, count = exact(self.request, 2)
            methods = exact(self.request, count)
            if ver != 5 or 2 not in methods:
                self.request.sendall(b"\x05\xff")
                return
            self.request.sendall(b"\x05\x02")
            version, user_len = exact(self.request, 2)
            username = exact(self.request, user_len)
            password = exact(self.request, exact(self.request, 1)[0])
            if (version != 1 or username != b"integration-user" or
                    password != b"integration-password-!@#-not-a-real-secret"):
                self.request.sendall(b"\x01\x01")
                return
            self.request.sendall(b"\x01\x00")
            metrics["authenticated_connections"] += 1
            version, command, reserved, kind = exact(self.request, 4)
            if version != 5 or command != 1 or reserved != 0:
                return
            if kind == 1:
                host = socket.inet_ntoa(exact(self.request, 4))
            elif kind == 3:
                host = exact(self.request, exact(self.request, 1)[0]).decode("ascii")
            elif kind == 4:
                host = socket.inet_ntop(socket.AF_INET6, exact(self.request, 16))
            else:
                return
            port = struct.unpack("!H", exact(self.request, 2))[0]
            if (host, port) == ("proxy.integration.test", 18080):
                target = ("127.0.0.1", 18080)
            elif (host, port) == ("example.com", 443):
                target = (host, port)
            else:
                self.request.sendall(b"\x05\x02\x00\x01" + b"\x00" * 6)
                return
            upstream = socket.create_connection(target, timeout=10)
            self.request.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            self.request.settimeout(None)
            upstream.settimeout(None)
            peers = [self.request, upstream]
            while True:
                readable, _, _ = select.select(peers, [], [], 30)
                if not readable:
                    return
                for peer in readable:
                    data = peer.recv(65536)
                    if not data:
                        return
                    (upstream if peer is self.request else self.request).sendall(data)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            if upstream is not None:
                upstream.close()

class ThreadedTCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        metrics["dns_queries"] += 1
        # Return NXDOMAIN. A DNS request reaching this listener is itself a leak.
        if len(data) >= 12:
            reply = data[:2] + b"\x81\x83" + data[4:6] + b"\x00" * 6 + data[12:]
            sock.sendto(reply, self.client_address)

class HTTPv6(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()

servers = [ThreadedTCP(("0.0.0.0", 1080), Socks),
           http.server.ThreadingHTTPServer(("0.0.0.0", 18080), Target),
           socketserver.ThreadingUDPServer(("0.0.0.0", 53), DNS)]
try:
    servers.append(HTTPv6(("::", 18080), Target))
except OSError:
    pass
for server in servers:
    threading.Thread(target=server.serve_forever, daemon=True).start()
print("mock-ready", flush=True)
threading.Event().wait()
'''


CONTROL = r'''
import http.server
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"control-still-reachable\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 3000), Handler).serve_forever()
'''


PROBE = r'''
import json, socket, struct, sys
ip, ip6, mode = sys.argv[1:4]

def exact(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count-len(data))
        if not part:
            raise EOFError()
        data += part
    return data

def proxy_request():
    with socket.create_connection(("127.0.0.1", 1080), timeout=4) as sock:
        sock.settimeout(4)
        sock.sendall(b"\x05\x01\x00")
        assert exact(sock, 2) == b"\x05\x00", "local relay negotiation failed"
        target = b"proxy.integration.test"
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(target)]) + target +
                     struct.pack("!H", 18080))
        header = exact(sock, 4)
        assert header[1] == 0, "relay rejected upstream connection"
        kind = header[3]
        exact(sock, 4 if kind == 1 else 16 if kind == 4 else exact(sock, 1)[0])
        exact(sock, 2)
        sock.sendall(b"GET /through-proxy HTTP/1.0\r\nHost: proxy.integration.test\r\n\r\n")
        body = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
        assert b"via-test-node" in body, "wrong proxied response"

def blocked_tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return False
    except OSError:
        return True

def blocked_dns(host):
    # A real, well-formed query catches Docker's embedded DNS special path.
    query = (b"\x73\x21\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" +
             b"\x07example\x03com\x00\x00\x01\x00\x01")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1)
        try:
            sock.sendto(query, (host, 53))
            sock.recvfrom(2048)
            return False
        except OSError:
            return True

results = {}
if mode == "online":
    proxy_request()
    results["proxy_request"] = True
else:
    try:
        proxy_request()
        results["proxy_unavailable"] = False
    except (OSError, AssertionError, EOFError):
        results["proxy_unavailable"] = True
results["direct_target_tcp_blocked"] = blocked_tcp(ip, 18080)
results["direct_upstream_socket_blocked"] = blocked_tcp(ip, 1080)
results["direct_public_tcp_blocked"] = blocked_tcp("1.1.1.1", 443)
results["external_dns_blocked"] = blocked_dns(ip)
results["public_dns_blocked"] = blocked_dns("1.1.1.1")
results["docker_dns_blocked"] = blocked_dns("127.0.0.11")
results["ipv6_blocked"] = blocked_tcp(ip6 or "2606:4700:4700::1111", 18080)
print(json.dumps(results, sort_keys=True))
if not all(results.values()):
    raise SystemExit(1)
'''


def docker(*args: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["docker", *args], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
    )
    if check and result.returncode:
        # Commands contain only disposable test credentials, never production data.
        raise RuntimeError(f"docker {args[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result


def wait_for(predicate, *, label: str, seconds: int = 45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {label}")


def main() -> None:
    token = uuid.uuid4().hex[:10]
    prefix = f"codex-proxy-test-{token}"
    network = prefix + "-network"
    image = prefix + "-gateway"
    mock = prefix + "-node"
    gateway = prefix + "-gateway"
    client = prefix + "-client"
    containers = []
    network_created = False
    image_created = False
    summary = {}
    with tempfile.TemporaryDirectory(prefix=prefix + "-", dir=ROOT.parent) as temp:
        temp_path = Path(temp)
        try:
            docker("info", timeout=20)
            if docker("image", "inspect", CLIENT_IMAGE, check=False).returncode:
                docker("pull", CLIENT_IMAGE, timeout=300)
            print("Building isolated gateway image...", flush=True)
            docker("build", "-t", image, "gateway", timeout=300)
            image_created = True
            subnet6 = f"fd{token[:2]}:{token[2:6]}:{token[6:10]}::/64"
            docker("network", "create", "--ipv6", "--subnet", subnet6, network)
            network_created = True
            mock_file = temp_path / "mock.py"
            mock_file.write_text(textwrap.dedent(MOCK))
            docker("run", "-d", "--name", mock, "--network", network,
                   "--mount", f"type=bind,src={mock_file},dst=/mock.py,readonly",
                   CLIENT_IMAGE, "python", "/mock.py")
            containers.append(mock)
            wait_for(lambda: "mock-ready" in docker("logs", mock).stdout,
                     label="mock SOCKS5 node")
            netinfo = json.loads(docker("inspect", mock).stdout)[0]["NetworkSettings"]["Networks"][network]
            mock_ip = netinfo["IPAddress"]
            mock_ip6 = netinfo.get("GlobalIPv6Address", "")
            config = temp_path / "proxy.json"
            config.write_text(json.dumps({
                "server": mock_ip, "port": 1080, "username": TEST_USER,
                "password": TEST_PASSWORD, "connect_timeout_seconds": 3,
            }))
            config.chmod(0o600)
            docker("run", "-d", "--name", gateway, "--network", network,
                   "--dns", mock_ip, "--cap-drop", "ALL",
                   "--cap-add", "NET_ADMIN", "--cap-add", "SETUID",
                   "--cap-add", "SETGID", "--cap-add", "DAC_OVERRIDE",
                   "--security-opt", "no-new-privileges:true",
                   "--read-only", "--tmpfs", "/tmp",
                   "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
                   "--sysctl", "net.ipv6.conf.default.disable_ipv6=1",
                   "--env", "XTABLES_LOCKFILE=/tmp/xtables.lock",
                   "--publish", "127.0.0.1::3000",
                   "--mount", f"type=bind,src={config},dst={CONFIG_PATH},readonly",
                   image)
            containers.append(gateway)
            print("Waiting for gateway's real upstream TLS check...", flush=True)

            def gateway_ready():
                result = docker("inspect", gateway)
                state = json.loads(result.stdout)[0]["State"]
                if not state["Running"]:
                    raise AssertionError("Gateway exited:\n" + docker("logs", gateway).stdout +
                                         docker("logs", gateway).stderr)
                if "Health" in state:
                    return state["Health"]["Status"] == "healthy"
                # A relay listener also covers Dockerfiles without a HEALTHCHECK.
                return docker("exec", gateway, "python", "-c",
                              "import socket; s=socket.create_connection(('127.0.0.1',1080),1); s.close()",
                              check=False).returncode == 0

            wait_for(gateway_ready, label="gateway", seconds=90)
            docker("run", "-d", "--name", client, "--network", "container:" + gateway,
                   "--cap-drop", "ALL", "--user", "1000:1000", CLIENT_IMAGE,
                   "python", "-u", "-c", CONTROL)
            containers.append(client)
            port_mapping = docker("port", gateway, "3000/tcp").stdout.strip()
            port = int(port_mapping.rsplit(":", 1)[1])
            control_url = f"http://127.0.0.1:{port}/"
            local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            control_marker = b"control-still-reachable\n"

            def control_ready():
                try:
                    with local_http.open(control_url, timeout=2) as response:
                        body = response.read()
                        return response.status == 200 and control_marker in body
                except OSError:
                    return False

            wait_for(control_ready, label="control ingress")
            print("Checking proxy, direct TCP, DNS and IPv6 paths...", flush=True)
            result = docker("exec", client, "python3", "-c", PROBE, mock_ip, mock_ip6,
                            "online", timeout=45, check=False)
            if result.returncode:
                raise AssertionError("Online isolation checks failed:\n" + result.stdout + result.stderr)
            summary["online"] = json.loads(result.stdout)
            metrics_command = (
                "import urllib.request; print(urllib.request.urlopen("
                "'http://127.0.0.1:18080/metrics').read().decode())"
            )
            metrics = json.loads(docker("exec", mock, "python", "-c", metrics_command).stdout)
            assert metrics["authenticated_connections"] > 0, "Upstream authentication not exercised"
            assert metrics["dns_queries"] == 0, f"DNS leaked to mock node: {metrics}"
            summary["upstream_authentication"] = True
            summary["no_dns_packets_reached_node"] = True
            environment = json.dumps(json.loads(docker("inspect", gateway).stdout)[0]["Config"]["Env"])
            log_result = docker("logs", gateway)
            logs = log_result.stdout + log_result.stderr
            for secret in (TEST_USER, TEST_PASSWORD):
                assert secret not in environment, "Credentials exposed through Docker environment"
                assert secret not in logs, "Credentials exposed through gateway logs"
            summary["credentials_absent_from_environment_and_logs"] = True
            if REAL_CHROME:
                print("Checking real Chrome UI and mandatory proxy policy...", flush=True)
                docker("rm", "-f", client)
                client = prefix + "-chrome"
                profile = temp_path / "browser-data"
                profile.mkdir()
                chrome_uid = os.getuid() or 1000
                chrome_gid = os.getgid() if os.getuid() else 1000
                policy = ROOT / "chrome" / "policies.json"
                assert policy.is_file(), "Managed policy file is missing"
                if docker("image", "inspect", CHROME_IMAGE, check=False).returncode:
                    docker("pull", CHROME_IMAGE, timeout=300)
                docker("run", "-d", "--name", client, "--network", "container:" + gateway,
                       "--cap-drop", "NET_ADMIN", "--cap-drop", "NET_RAW",
                       "--cap-drop", "SYS_ADMIN", "--shm-size", "1gb",
                       "--env", f"PUID={chrome_uid}", "--env", f"PGID={chrome_gid}",
                       "--mount", f"type=bind,src={profile},dst=/config",
                       "--mount", f"type=bind,src={policy},dst=/etc/opt/chrome/policies/managed/forced-proxy.json,readonly",
                       CHROME_IMAGE)
                containers.append(client)
                control_marker = b"<html"
                wait_for(control_ready, label="real Chrome control UI", seconds=90)
                wait_for(lambda: docker("exec", client, "pgrep", "-x", "chrome", check=False).returncode == 0,
                         label="real Chrome process", seconds=45)
                browser = docker("exec", "--user", f"{chrome_uid}:{chrome_gid}", client,
                                 "google-chrome", "--headless", "--no-sandbox", "--disable-gpu",
                                 "--user-data-dir=/tmp/policy-probe", "--dump-dom",
                                 "http://proxy.integration.test:18080/", timeout=45, check=False)
                assert browser.returncode == 0 and "via-test-node" in browser.stdout, (
                    "Real Chrome did not use the managed proxy policy:\n" + browser.stdout + browser.stderr)
                summary["real_chrome_control_ui"] = True
                summary["real_chrome_managed_proxy_policy"] = True
                summary["real_chrome_nonroot_uid"] = chrome_uid
                summary["real_chrome_dropped_net_admin_and_net_raw"] = True
                result = docker("exec", client, "python3", "-c", PROBE, mock_ip, mock_ip6,
                                "online", timeout=45, check=False)
                if result.returncode:
                    raise AssertionError("Real Chrome isolation failed:\n" + result.stdout + result.stderr)
                summary["real_chrome_isolation"] = json.loads(result.stdout)
                metrics = json.loads(docker("exec", mock, "python", "-c", metrics_command).stdout)
                assert metrics["dns_queries"] == 0, f"Real Chrome leaked DNS: {metrics}"
            print("Stopping mock upstream and checking fail-closed behavior...", flush=True)
            docker("stop", "--time", "1", mock)
            result = docker("exec", client, "python3", "-c", PROBE, mock_ip, mock_ip6,
                            "offline", timeout=45, check=False)
            if result.returncode:
                raise AssertionError("Offline isolation checks failed:\n" + result.stdout + result.stderr)
            summary["offline"] = json.loads(result.stdout)
            assert control_ready(), "Control ingress stopped working after proxy loss"
            summary["control_survives_proxy_loss"] = True
            print("Stopping gateway and checking the surviving namespace...", flush=True)
            docker("stop", "--time", "1", gateway)
            result = docker("exec", client, "python3", "-c", PROBE, mock_ip, mock_ip6,
                            "offline", timeout=45, check=False)
            if result.returncode:
                raise AssertionError("Gateway-stop isolation failed:\n" + result.stdout + result.stderr)
            summary["gateway_stopped_isolation"] = json.loads(result.stdout)
            check_control = docker("exec", client, "python3", "-c",
                                   "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:3000/',timeout=2).status==200")
            summary["control_inside_surviving_namespace"] = check_control.returncode == 0
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        finally:
            for container in reversed(containers):
                docker("rm", "-f", container, timeout=20, check=False)
            if network_created:
                docker("network", "rm", network, timeout=20, check=False)
            if image_created:
                docker("image", "rm", image, timeout=30, check=False)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
