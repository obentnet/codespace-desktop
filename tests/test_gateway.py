import contextlib
import json
from pathlib import Path
import socket
import socketserver
import struct
import tempfile
import threading
import time
import unittest

from gateway.config import ConfigError, ProxyConfig, load_config
from gateway.relay import RelayServer, SocksError, connect_request, read_reply, receive_exact, upstream_connect


def recv(stream, length):
    return receive_exact(stream, length, time.monotonic() + 3)


@contextlib.contextmanager
def fake_node(handler):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            handler(self.request)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=3)


@contextlib.contextmanager
def local_relay(config):
    with RelayServer(("127.0.0.1", 0), config) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=3)


class ConfigTests(unittest.TestCase):
    def parse(self, changes=None):
        data = {"server": "203.0.113.10", "port": 1080, "username": "", "password": ""}
        data.update(changes or {})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_config(path)

    def test_valid_auth_and_defaults(self):
        config = self.parse({"username": "用户", "password": "a:$()\"\\@ secret"})
        self.assertEqual(config.username, "用户")
        self.assertEqual(config.password, "a:$()\"\\@ secret")
        self.assertEqual(config.connect_timeout_seconds, 15)

    def test_bad_or_nonliteral_server_rejected(self):
        for server in ("", "proxy.example.com", "127.0.0.1", "0.0.0.0", "224.0.0.1", "169.254.1.1", "255.255.255.255", "::1", True):
            with self.subTest(server=server), self.assertRaises(ConfigError):
                self.parse({"server": server})

    def test_types_ranges_and_unknown_keys(self):
        for patch in ({"port": True}, {"port": 0}, {"port": 65536}, {"port": "1080"}, {"connect_timeout_seconds": False}, {"connect_timeout_seconds": 61}, {"password": "x"}, {"username": "x"}, {"unexpected": "secret"}, {"username": "x", "password": "字" * 86}):
            with self.subTest(patch=patch), self.assertRaises(ConfigError) as caught:
                self.parse(patch)
            self.assertNotIn("secret", str(caught.exception))

    def test_malformed_json_does_not_echo_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy.json"
            path.write_text('{"password": "do-not-log-this", BROKEN}', encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load_config(path)
            self.assertNotIn("do-not-log-this", str(caught.exception))


class ProtocolTests(unittest.TestCase):
    def test_anonymous_upstream_connect_and_data_round_trip(self):
        received = []
        request = connect_request("anonymous-upstream.invalid", 443)

        def node(stream):
            received.append(recv(stream, 3))
            stream.sendall(b"\x05\x00")
            # An anonymous upstream must receive CONNECT immediately, without
            # any RFC 1929 username/password packet between these messages.
            received.append(recv(stream, len(request)))
            stream.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            stream.sendall(recv(stream, 9)[::-1])

        with fake_node(node) as node_port:
            config = ProxyConfig("127.0.0.1", node_port, "", "", 3)
            with local_relay(config) as relay_port:
                with socket.create_connection(("127.0.0.1", relay_port), 3) as client:
                    client.sendall(b"\x05\x01\x00")
                    self.assertEqual(recv(client, 2), b"\x05\x00")
                    client.sendall(request)
                    self.assertEqual(read_reply(client, time.monotonic() + 3)[1], 0)
                    client.sendall(b"anonymous")
                    self.assertEqual(recv(client, 9), b"suomynona")
        self.assertEqual(received, [b"\x05\x01\x00", request])

    def test_authenticated_upstream_preserves_domain_and_data(self):
        received = []
        request = connect_request("not-resolved-locally.invalid", 443)

        def node(stream):
            received.append(recv(stream, 3))
            stream.sendall(b"\x05\x02")
            version, length = recv(stream, 2)
            username = recv(stream, length)
            password = recv(stream, recv(stream, 1)[0])
            received.append((version, username, password))
            stream.sendall(b"\x01\x00")
            received.append(recv(stream, len(request)))
            stream.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
            data = recv(stream, 4)
            stream.sendall(data.upper())

        with fake_node(node) as node_port:
            config = ProxyConfig("127.0.0.1", node_port, "alice", "p:@ss", 3)
            with local_relay(config) as relay_port:
                with socket.create_connection(("127.0.0.1", relay_port), 3) as client:
                    client.sendall(b"\x05\x01\x00")
                    self.assertEqual(recv(client, 2), b"\x05\x00")
                    client.sendall(request)
                    self.assertEqual(read_reply(client, time.monotonic() + 3)[1], 0)
                    client.sendall(b"ping")
                    self.assertEqual(recv(client, 4), b"PING")
        self.assertEqual(received[0], b"\x05\x01\x02")
        self.assertEqual(received[1], (1, b"alice", b"p:@ss"))
        self.assertEqual(received[2], request)

    def test_auth_downgrade_refused(self):
        extra = []

        def node(stream):
            recv(stream, 3)
            stream.sendall(b"\x05\x00")
            extra.append(stream.recv(1))

        with fake_node(node) as port:
            with self.assertRaises(SocksError):
                upstream_connect(ProxyConfig("127.0.0.1", port, "alice", "secret", 3), connect_request("example.com", 443))
        self.assertEqual(extra, [b""])

    def test_upstream_auth_failure_never_sends_connect(self):
        extra = []

        def node(stream):
            recv(stream, 3)
            stream.sendall(b"\x05\x02")
            _, size = recv(stream, 2)
            recv(stream, size)
            recv(stream, recv(stream, 1)[0])
            stream.sendall(b"\x01\x01")
            extra.append(stream.recv(1))

        with fake_node(node) as port:
            with self.assertRaises(SocksError):
                upstream_connect(ProxyConfig("127.0.0.1", port, "alice", "secret", 3), connect_request("example.com", 443))
        self.assertEqual(extra, [b""])

    def test_bind_and_udp_are_rejected_without_upstream(self):
        config = ProxyConfig("127.0.0.1", 1, connect_timeout_seconds=2)
        with local_relay(config) as port:
            for command in (2, 3):
                with socket.create_connection(("127.0.0.1", port), 2) as client:
                    client.sendall(b"\x05\x01\x00")
                    self.assertEqual(recv(client, 2), b"\x05\x00")
                    client.sendall(bytes((5, command, 0, 1)) + b"\x00" * 6)
                    self.assertEqual(recv(client, 10)[1], 7)

    def test_downstream_proxy_failure_returns_socks_failure(self):
        with local_relay(ProxyConfig("127.0.0.1", 1, connect_timeout_seconds=1)) as port:
            with socket.create_connection(("127.0.0.1", port), 2) as client:
                client.sendall(b"\x05\x01\x00")
                self.assertEqual(recv(client, 2), b"\x05\x00")
                client.sendall(connect_request("example.com", 443))
                self.assertNotEqual(recv(client, 10)[1], 0)


if __name__ == "__main__":
    unittest.main()
