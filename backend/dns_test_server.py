"""Tiny local DNS server for offline DNS-leak tests.

Prefers `dnslib` when available; otherwise falls back to a minimal stdlib
UDP DNS responder that answers A queries with a fixed TEST-ADDRESS IP.
"""

import socket
import socketserver
import struct
import threading

DNS_TEST_IP = "203.0.113.1"


def _skip_name(buf: bytes, offset: int = 0) -> int:
    """Return the index just past a DNS domain name (handles basic compression)."""
    i = offset
    while i < len(buf):
        length = buf[i]
        if length & 0xC0:
            # Pointer to somewhere earlier in the packet; pointer is 2 bytes.
            return i + 2
        if length == 0:
            return i + 1
        i += length + 1
    return i


def _build_dns_response(query: bytes, ip_str: str = DNS_TEST_IP) -> bytes:
    """Build a minimal DNS response for an A query.

    Only the first question is inspected; all other questions are ignored.
    Non-A queries receive an empty NOERROR answer.
    """
    if len(query) < 12:
        return b""

    transaction_id = query[:2]
    qdcount = struct.unpack(">H", query[4:6])[0]
    if qdcount < 1:
        return b""

    # Standard response flags: response, authoritative answer, recursion desired/available.
    flags = struct.pack(">H", 0x8580)
    ancount = struct.pack(">H", 0)
    nscount = struct.pack(">H", 0)
    arcount = struct.pack(">H", 0)

    # Parse the first question copied verbatim into the response body.
    question_start = 12
    name_end = _skip_name(query, question_start)
    if name_end + 4 > len(query):
        return b""

    qname = query[question_start:name_end]
    qtype, qclass = struct.unpack(">HH", query[name_end : name_end + 4])

    answer = b""
    if qtype == 1:  # A record
        ip_bytes = socket.inet_aton(ip_str)
        # Name pointer back to the question name at offset 12.
        answer = (
            b"\xc0\x0c"
            + struct.pack(">HH", 1, 1)        # Type A, Class IN
            + struct.pack(">I", 300)          # TTL
            + struct.pack(">H", 4)            # RDLENGTH
            + ip_bytes
        )
        ancount = struct.pack(">H", 1)

    header = (
        transaction_id
        + flags
        + struct.pack(">H", qdcount)
        + ancount
        + nscount
        + arcount
    )
    return header + qname + struct.pack(">HH", qtype, qclass) + answer


class _StdlibDNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        if not data:
            return
        response = _build_dns_response(data)
        if response:
            sock.sendto(response, self.client_address)


class _StdlibDNSServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True


def start_dns_test_server(port: int = 0):
    """Start a test DNS server on 127.0.0.1.

    Returns (bound_port:int, stop:Callable[[], None]).
    """
    # Try dnslib first; no hard dependency.
    try:
        import dnslib.server as _dnsserver  # type: ignore
        from dnslib import DNSRecord, QTYPE, A, RR  # type: ignore
    except Exception:
        _dnsserver = None  # type: ignore

    if _dnsserver:
        class _DnslibResolver(_dnsserver.BaseResolver):
            def resolve(self, request, handler):
                reply = request.reply()
                if QTYPE.get(request.q.qtype) == "A":
                    reply.add_answer(
                        RR(request.q.qname, QTYPE.A, rdata=A(DNS_TEST_IP), ttl=300)
                    )
                return reply

        server = _dnsserver.DNSServer(
            _DnslibResolver(),
            port=port,
            address="127.0.0.1",
            logger=None,
        )
        server.start_thread()
        # DNSServer exposes the underlying UDP socket through .server.address.
        bound_port = server.server.address[1]

        def _stop():
            try:
                server.stop()
            except Exception:
                pass

        return bound_port, _stop

    # Stdlib fallback.
    server = _StdlibDNSServer(("127.0.0.1", port), _StdlibDNSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _stop():
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        thread.join(timeout=2.0)

    return server.server_address[1], _stop


if __name__ == "__main__":
    import sys

    port, stop = start_dns_test_server()
    print(f"Test DNS server listening on 127.0.0.1:{port}")
    print("Press Enter to stop...")
    # On Windows, sys.stdin.readline() blocks; use a short manual loop so Ctrl+C works.
    try:
        sys.stdin.readline()
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        print("Stopped.")
