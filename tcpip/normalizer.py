"""User-space TCP relay/normalizer.

Because raw SYN/TCP option control is not available from Python sockets, this
module provides a local TCP relay that ensures traffic exits through a known
upstream proxy and logs connection attempts.
"""
from __future__ import annotations

import select
import socket
import threading
from typing import Any, Callable, Dict, Optional


def _parse_proxy_url(proxy_url: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse http://host:port or socks5://host:port into a dict."""
    if not proxy_url:
        return None
    scheme = "http"
    rest = proxy_url
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
    if "/" in rest:
        rest = rest.split("/", 1)[0]
    host_part = rest
    port = 8080 if scheme.startswith("http") else 1080
    if ":" in host_part:
        host_part, port_str = host_part.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 8080
    return {"scheme": scheme.lower(), "host": host_part, "port": port}


def _relay(local_sock: socket.socket, remote_sock: socket.socket) -> None:
    try:
        while True:
            ready, _, _ = select.select([local_sock, remote_sock], [], [], 30.0)
            if not ready:
                break
            for src, dst in ((local_sock, remote_sock), (remote_sock, local_sock)):
                try:
                    data = src.recv(65535)
                except OSError:
                    return
                if not data:
                    return
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        for s in (local_sock, remote_sock):
            try:
                s.close()
            except OSError:
                pass


def run_tcp_normalizer(
    listen_host: str = "127.0.0.1",
    listen_port: int = 0,
    upstream_proxy: Optional[str] = None,
    destination_host: Optional[str] = None,
    destination_port: int = 443,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Start a local TCP relay.

    If ``upstream_proxy`` is set, outbound connections go to the proxy.
    Otherwise they go directly to ``destination_host``/``destination_port``.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_host, listen_port))
    server.listen(5)
    bound_host, bound_port = server.getsockname()
    stop_event = threading.Event()
    proxy_info = _parse_proxy_url(upstream_proxy)

    def log(msg: str) -> None:
        if logger:
            logger(msg)

    def handle_client(client: socket.socket, addr: Any) -> None:
        try:
            target_host = proxy_info["host"] if proxy_info else (destination_host or "127.0.0.1")
            target_port = proxy_info["port"] if proxy_info else destination_port
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.settimeout(10)
            remote.connect((target_host, target_port))
            log(f"Normalized connection from {addr} -> {target_host}:{target_port}")
            _relay(client, remote)
        except Exception as exc:
            log(f"Normalizer connection error: {exc}")
            try:
                client.close()
            except OSError:
                pass

    def loop() -> None:
        server.settimeout(1.0)
        while not stop_event.is_set():
            try:
                client, addr = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=handle_client, args=(client, addr), daemon=True).start()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

    def stop() -> None:
        stop_event.set()
        try:
            server.close()
        except OSError:
            pass
        thread.join(timeout=2.0)

    return {
        "host": bound_host,
        "port": bound_port,
        "upstream": proxy_info,
        "stop": stop,
        "thread": thread,
    }
