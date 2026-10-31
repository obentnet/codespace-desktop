"""CONNECT-only SOCKS5 relay. The only external socket is the configured node."""

import select
import socket
import socketserver
import ssl
import struct
import threading
import time

try:
    from .config import ProxyConfig
except ImportError:
    from config import ProxyConfig


class SocksError(OSError):
    def __init__(self, message="SOCKS5 negotiation failed", reply=1):
        super().__init__(message)
        self.reply = reply


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("SOCKS5 connection timed out")
    return seconds


def receive_exact(stream, size, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        stream.settimeout(remaining(deadline))
        chunk = stream.recv(size - len(chunks))
        if not chunk:
            raise SocksError("SOCKS5 connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def send_bytes(stream, data, deadline):
    stream.settimeout(remaining(deadline))
    stream.sendall(data)


def read_address(stream, kind, deadline):
    if kind == 1:
        return receive_exact(stream, 4, deadline)
    if kind == 4:
        return receive_exact(stream, 16, deadline)
    if kind == 3:
        length = receive_exact(stream, 1, deadline)
        if not length[0]:
            raise SocksError("Invalid SOCKS5 domain", reply=8)
        return length + receive_exact(stream, length[0], deadline)
    raise SocksError("Unsupported SOCKS5 address type", reply=8)


def read_reply(stream, deadline):
    header = receive_exact(stream, 4, deadline)
    if header[0] != 5 or header[2] != 0:
        raise SocksError("Invalid upstream SOCKS5 reply")
    address = read_address(stream, header[3], deadline)
    port = receive_exact(stream, 2, deadline)
    if header[1] != 0:
        raise SocksError("Upstream refused SOCKS5 CONNECT", reply=header[1] if 1 <= header[1] <= 8 else 1)
    return header + address + port


def upstream_connect(config, request, deadline=None):
    """request is an already encoded SOCKS5 CONNECT request; never resolve it."""
    if deadline is None:
        deadline = time.monotonic() + config.connect_timeout_seconds
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        upstream.settimeout(remaining(deadline))
        upstream.connect((config.server, config.port))
        method = 2 if config.username else 0
        send_bytes(upstream, bytes((5, 1, method)), deadline)
        if receive_exact(upstream, 2, deadline) != bytes((5, method)):
            raise SocksError("Upstream authentication method rejected")
        if method == 2:
            username = config.username.encode("utf-8")
            password = config.password.encode("utf-8")
            send_bytes(upstream, bytes((1, len(username))) + username + bytes((len(password),)) + password, deadline)
            if receive_exact(upstream, 2, deadline) != b"\x01\x00":
                raise SocksError("Upstream authentication rejected")
        send_bytes(upstream, request, deadline)
        response = read_reply(upstream, deadline)
        return upstream, response
    except BaseException:
        upstream.close()
        raise


def connect_request(host, port):
    name = host.encode("idna")
    if not 0 < len(name) <= 255:
        raise SocksError("Invalid probe hostname")
    return b"\x05\x01\x00\x03" + bytes((len(name),)) + name + struct.pack("!H", port)


def probe_stream(stream, deadline):
    """Verify actual connectivity, remote DNS and TLS, within one deadline."""
    stream.settimeout(remaining(deadline))
    with ssl.create_default_context().wrap_socket(stream, server_hostname="example.com") as tls:
        send_bytes(tls, b"HEAD / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n", deadline)
        status = bytearray()
        while b"\r\n" not in status and len(status) < 512:
            tls.settimeout(remaining(deadline))
            chunk = tls.recv(512 - len(status))
            if not chunk:
                break
            status.extend(chunk)
        line = bytes(status).split(b"\r\n", 1)[0].split(b" ")
        if len(line) < 2 or line[0] not in (b"HTTP/1.0", b"HTTP/1.1") or not line[1].isdigit() or not 200 <= int(line[1]) < 400:
            raise SocksError("Proxy connectivity check did not return a successful HTTP response")


def probe_upstream(config):
    deadline = time.monotonic() + min(config.connect_timeout_seconds, 15)
    stream, _ = upstream_connect(config, connect_request("example.com", 443), deadline)
    try:
        probe_stream(stream, deadline)
    finally:
        stream.close()


def probe_local(timeout=12):
    deadline = time.monotonic() + timeout
    with socket.create_connection(("127.0.0.1", 1080), remaining(deadline)) as stream:
        send_bytes(stream, b"\x05\x01\x00", deadline)
        if receive_exact(stream, 2, deadline) != b"\x05\x00":
            raise SocksError("Local SOCKS5 relay is unavailable")
        send_bytes(stream, connect_request("example.com", 443), deadline)
        read_reply(stream, deadline)
        probe_stream(stream, deadline)


def copy_connection(client, upstream, idle_timeout=300):
    """Bounded per-connection threads and buffers with half-close support."""
    readers = [client, upstream]
    peers = {client: upstream, upstream: client}
    last_activity = time.monotonic()
    for stream in readers:
        stream.settimeout(30)
    while readers:
        if time.monotonic() - last_activity >= idle_timeout:
            return
        ready, _, _ = select.select(readers, [], [], min(30, idle_timeout))
        for stream in ready:
            chunk = stream.recv(65536)
            if chunk:
                peers[stream].sendall(chunk)
                last_activity = time.monotonic()
            else:
                readers.remove(stream)
                try:
                    peers[stream].shutdown(socket.SHUT_WR)
                except OSError:
                    pass


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.connection_slots.acquire(blocking=False):
            return
        upstream = None
        negotiated = False
        deadline = time.monotonic() + self.server.config.connect_timeout_seconds
        try:
            header = receive_exact(self.request, 2, deadline)
            if header[0] != 5 or header[1] == 0:
                return
            methods = receive_exact(self.request, header[1], deadline)
            if 0 not in methods:
                send_bytes(self.request, b"\x05\xff", deadline)
                return
            send_bytes(self.request, b"\x05\x00", deadline)
            negotiated = True
            header = receive_exact(self.request, 4, deadline)
            if header[0] != 5 or header[2] != 0:
                raise SocksError("Invalid SOCKS5 request")
            if header[1] != 1:
                raise SocksError("Only SOCKS5 CONNECT is supported", reply=7)
            address = read_address(self.request, header[3], deadline)
            port = receive_exact(self.request, 2, deadline)
            upstream, reply = upstream_connect(self.server.config, header + address + port, deadline)
            send_bytes(self.request, reply, deadline)
            negotiated = False
            copy_connection(self.request, upstream)
        except (OSError, ValueError) as exc:
            if negotiated:
                try:
                    reply_code = getattr(exc, "reply", 1)
                    send_bytes(self.request, bytes((5, reply_code, 0, 1)) + b"\x00" * 6, time.monotonic() + 1)
                except OSError:
                    pass
            # Destination names and credentials are deliberately not logged.
        finally:
            if upstream is not None:
                upstream.close()
            self.server.connection_slots.release()


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address, config):
        self.config = config
        self.connection_slots = threading.BoundedSemaphore(128)
        super().__init__(address, RelayHandler)

    def handle_error(self, request, client_address):
        # The default implementation prints a traceback with request information.
        pass
