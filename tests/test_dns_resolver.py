"""Tests for tcpip.dns_resolver."""
from __future__ import annotations

import socket
from unittest import TestCase

from tcpip.dns_resolver import parse_dns_packet_qname


class DnsResolverTests(TestCase):
    def test_parse_dns_packet_qname_simple(self):
        # Build a minimal DNS query for example.com
        packet = bytearray(12)
        labels = b"\x07example\x03com\x00"
        packet.extend(labels)
        packet.extend(b"\x00\x01")  # A
        packet.extend(b"\x00\x01")  # IN
        self.assertEqual(parse_dns_packet_qname(bytes(packet)), "example.com")

    def test_parse_dns_packet_qname_invalid_returns_none(self):
        self.assertIsNone(parse_dns_packet_qname(b""))

    def test_is_aaaa_query(self):
        from tcpip.dns_resolver import _is_aaaa_query
        # Minimal A record packet
        packet = bytearray(12)
        packet[4:6] = b"\x00\x01"
        packet.extend(b"\x03www\x00\x00\x01\x00\x01")
        self.assertFalse(_is_aaaa_query(bytes(packet)))

    def test_dns_proxy_returns_port(self):
        from tcpip.dns_resolver import start_dns_proxy
        proxy = start_dns_proxy(
            upstream_host="127.0.0.1",
            upstream_port=53,
            listen_port=0,
            drop_aaaa=True,
        )
        self.assertIsNotNone(proxy["port"])
        self.assertGreater(proxy["port"], 0)
        proxy["stop"]()


class TcpipManagerConfigTests(TestCase):
    def test_manager_disabled_by_default(self):
        import os
        import shutil
        os.environ.pop("GHOSTBROWSER_ENABLE_TCPIP_MANAGER", None)
        from backend.tcpip_manager import TcpIpManager
        mgr = TcpIpManager()
        self.assertFalse(mgr.enable)

    def test_manager_enabled_via_env(self):
        import os
        os.environ["GHOSTBROWSER_ENABLE_TCPIP_MANAGER"] = "1"
        os.environ["GHOSTBROWSER_DNS_SERVER"] = "1.1.1.1"
        from backend.tcpip_manager import TcpIpManager
        mgr = TcpIpManager()
        self.assertTrue(mgr.enable)
        self.assertEqual(mgr.dns_host, "1.1.1.1")
