"""Local UDP DNS forwarder with optional AAAA drop.

Uses only stdlib sockets. Not a full DNS server: it simply forwards DNS
messages to an upstream resolver and relays the answer back.
"""
from __future__ import annotations

import select
import socket
import threading
from typing import Any, Callable, Dict, Optional


def _is_aaaa_query(packet: bytes) -> bool:
    """Heuristic: return True if the DNS query is for an AAAA record."""
    if len(packet) < 12:
        return False
    qdcount = (packet[4] << 8) | packet[5]
    offset = 12
    for _ in range(qdcount):
        while offset < len(packet) and packet[offset] != 0:
            offset += 1 + packet[offset]
        if offset >= len(packet):
            return False
        offset += 1
        if offset + 4 > len(packet):
            return False
        qtype = (packet[offset] << 8) | packet[offset + 1]
        if qtype == 28:  # AAAA
            return True
        offset += 4
    return False


def start_dns_proxy(
    upstream_host: str = "127.0.0.1",
    upstream_port: int = 53,
    listen_host: str = "127.0.0.1",
    listen_port: int = 0,
    drop_aaaa: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Start a non-blocking UDP DNS forwarder and return its info.

    The caller must call ``stop()`` on the returned dict to terminate the
    background thread.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((listen_host, listen_port))
    sock.setblocking(False)
    bound_host, bound_port = sock.getsockname()
    stop_event = threading.Event()

    def log(msg: str) -> None:
        if logger:
            logger(msg)

    def loop() -> None:
        upstream = (upstream_host, upstream_port)
        pending: Dict[int, Any] = {}
        counter = 0
        while not stop_event.is_set():
            ready, _, _ = select.select([sock], [], [], 0.5)
            if not ready:
                continue
            try:
                data, addr = sock.recvfrom(65535)
            except OSError:
                continue
            if drop_aaaa and _is_aaaa_query(data):
                log(f"Dropped AAAA query from {addr}")
                continue
            try:
                counter += 1
                txn_id = counter & 0xFFFF
                pending[txn_id] = addr
                sock.sendto(data, upstream)
                # Wait briefly for reply
                ack_ready, _, _ = select.select([sock], [], [], 2.0)
                if ack_ready:
                    reply, _ = sock.recvfrom(65535)
                    sock.sendto(reply, addr)
            except OSError as exc:
                log(f"DNS proxy error: {exc}")

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

    def stop() -> None:
        stop_event.set()
        thread.join(timeout=1.0)
        try:
            sock.close()
        except OSError:
            pass

    return {
        "host": bound_host,
        "port": bound_port,
        "socket": sock,
        "stop": stop,
        "thread": thread,
    }


def parse_dns_packet_qname(packet: bytes) -> Optional[str]:
    """Return the first queried domain name, or None."""
    try:
        offset = 12
        labels = []
        while offset < len(packet):
            length = packet[offset]
            offset += 1
            if length == 0:
                break
            labels.append(packet[offset:offset + length].decode("ascii"))
            offset += length
        return ".".join(labels) or None
    except Exception:
        return None
