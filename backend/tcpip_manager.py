"""Optional TCP/IP fingerprint control manager."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from backend.logging_config import logger

_tcpip_manager_instance: Optional["TcpIpManager"] = None


def set_manager(manager: "TcpIpManager") -> None:
    global _tcpip_manager_instance
    _tcpip_manager_instance = manager


def get_manager() -> Optional["TcpIpManager"]:
    return _tcpip_manager_instance


class TcpIpManager:
    """Starts DNS/TCP normalizer helpers based on environment config."""

    def __init__(
        self,
        enable: bool = False,
        upstream_dns_host: Optional[str] = None,
        upstream_dns_port: Optional[int] = None,
        drop_aaaa: bool = True,
        proxy_relay_port: Optional[int] = None,
        upstream_proxy: Optional[str] = None,
    ):
        self.enable = enable or self._env_bool("GHOSTBROWSER_ENABLE_TCPIP_MANAGER")
        dns_from_env = upstream_dns_host or os.environ.get("GHOSTBROWSER_DNS_SERVER", "127.0.0.1")
        if ":" in dns_from_env:
            host, port_str = dns_from_env.rsplit(":", 1)
            try:
                self.dns_host = host
                self.dns_port = int(port_str)
            except ValueError:
                self.dns_host = dns_from_env
                self.dns_port = int(upstream_dns_port or "53")
        else:
            self.dns_host = dns_from_env
            try:
                self.dns_port = int(upstream_dns_port or os.environ.get("GHOSTBROWSER_DNS_PORT", "53") or "53")
            except ValueError:
                self.dns_port = 53
        try:
            self.proxy_relay_port = int(
                proxy_relay_port
                if proxy_relay_port is not None
                else os.environ.get("GHOSTBROWSER_TCPIP_NORMALIZER_PORT", os.environ.get("GHOSTBROWSER_TCPIP_RELAY_PORT", "0")) or "0"
            )
        except ValueError:
            self.proxy_relay_port = 0
        drop_env = os.environ.get("GHOSTBROWSER_TCPIP_DROP_AAAA", "").strip().lower()
        self.drop_aaaa = drop_aaaa if drop_env == "" else (drop_env in {"1", "true", "yes"})
        self.upstream_proxy = upstream_proxy or os.environ.get("GHOSTBROWSER_UPSTREAM_PROXY_NORMALIZER", "")
        self._dns_proxy: Optional[Dict[str, Any]] = None
        self._tcp_relay: Optional[Dict[str, Any]] = None

    @staticmethod
    def _env_bool(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}

    async def start(self) -> Dict[str, Any]:
        if not self.enable:
            return {"enabled": False, "message": "TcpIpManager is disabled"}
        try:
            from tcpip.dns_resolver import start_dns_proxy
            from tcpip.normalizer import run_tcp_normalizer

            self._dns_proxy = start_dns_proxy(
                upstream_host=self.dns_host,
                upstream_port=self.dns_port,
                listen_port=0,
                drop_aaaa=self.drop_aaaa,
                logger=lambda msg: logger.info("[DNS proxy] %s", msg),
            )
            logger.info("DNS proxy started on %s:%s", self._dns_proxy["host"], self._dns_proxy["port"])

            self._tcp_relay = run_tcp_normalizer(
                listen_port=self.proxy_relay_port,
                upstream_proxy=self.upstream_proxy or None,
                logger=lambda msg: logger.info("[TCP relay] %s", msg),
            )
            logger.info("TCP relay started on %s:%s", self._tcp_relay["host"], self._tcp_relay["port"])
        except Exception as exc:
            logger.error("TcpIpManager start failed: %s", exc)
            return {"enabled": True, "error": "Failed to start TCP/IP normalization services"}
        return {"enabled": True, "status": self.get_status()}

    def stop(self) -> None:
        try:
            if self._dns_proxy:
                self._dns_proxy["stop"]()
            if self._tcp_relay:
                self._tcp_relay["stop"]()
        except Exception as exc:
            logger.error("TcpIpManager stop failed: %s", exc)
        finally:
            self._dns_proxy = None
            self._tcp_relay = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enable,
            "dns": {
                "host": self._dns_proxy["host"] if self._dns_proxy else None,
                "port": self._dns_proxy["port"] if self._dns_proxy else None,
                "upstream": f"{self.dns_host}:{self.dns_port}" if self.enable else None,
                "drop_aaaa": self.drop_aaaa,
            },
            "tcp_relay": {
                "host": self._tcp_relay["host"] if self._tcp_relay else None,
                "port": self._tcp_relay["port"] if self._tcp_relay else None,
                "upstream_proxy": self.upstream_proxy or None,
            },
        }
