"""Tests for backend.tcpip_manager."""
from __future__ import annotations

import os
from unittest import TestCase

from backend.tcpip_manager import TcpIpManager


class TcpIpManagerTests(TestCase):
    def test_disabled_without_env(self):
        os.environ.pop("GHOSTBROWSER_ENABLE_TCPIP_MANAGER", None)
        mgr = TcpIpManager()
        self.assertFalse(mgr.enable)

    def test_enabled_with_env(self):
        os.environ["GHOSTBROWSER_ENABLE_TCPIP_MANAGER"] = "1"
        os.environ["GHOSTBROWSER_DNS_SERVER"] = "1.1.1.1"
        mgr = TcpIpManager()
        self.assertTrue(mgr.enable)
        self.assertEqual(mgr.dns_host, "1.1.1.1")
        self.assertEqual(mgr.dns_port, 53)

    def test_status_when_disabled(self):
        os.environ.pop("GHOSTBROWSER_ENABLE_TCPIP_MANAGER", None)
        mgr = TcpIpManager()
        status = mgr.get_status()
        self.assertFalse(status["enabled"])
        self.assertIsNone(status["dns"]["port"])
