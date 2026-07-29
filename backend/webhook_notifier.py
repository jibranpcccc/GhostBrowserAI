"""Lightweight in-memory async webhook notifier.

Supported events:
  - profile.launch
  - profile.close
  - proxy.failure

Usage (best-effort fire-and-forget):
    from backend.webhook_notifier import webhook_notifier
    webhook_notifier.notify("profile.launch", {"profile_id": profile_id})
"""

import asyncio
import json
import logging
import os
import threading
import urllib.request
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS: List[str] = [
    "profile.launch",
    "profile.close",
    "proxy.failure",
]


def _has_aiohttp() -> bool:
    try:
        import aiohttp  # noqa: F401
        return True
    except Exception:
        return False


class WebhookNotifier:
    """In-memory webhook URL registry and lightweight async notification."""

    def __init__(self):
        # URL -> set of subscribed events
        self._hooks: Dict[str, set] = {}
        self._aiohttp_session: Optional[object] = None

    def register(self, url: str, events: List[str]) -> None:
        """Register a webhook URL for one or more supported events."""
        if not url or not isinstance(url, str):
            logger.warning("Webhook registration skipped: invalid URL")
            return
        validated_events = []
        for event in events or []:
            if event not in SUPPORTED_EVENTS:
                logger.warning("Webhook registration skipped unknown event: %s", event)
                continue
            validated_events.append(event)
        if not validated_events:
            logger.warning("Webhook registration skipped: no supported events for %s", url)
            return
        self._hooks.setdefault(url, set()).update(validated_events)
        logger.info("Registered webhook %s for events %s", url, validated_events)

    def unregister(self, url: str) -> bool:
        """Remove a webhook URL from the registry."""
        if url in self._hooks:
            del self._hooks[url]
            logger.info("Unregistered webhook %s", url)
            return True
        return False

    def list_hooks(self) -> List[Dict[str, object]]:
        """Return configured webhook registrations."""
        return [
            {"url": url, "events": sorted(list(events))}
            for url, events in self._hooks.items()
        ]

    def notify(self, event: str, payload: Dict[str, object]) -> None:
        """Notify every registered webhook subscribed to ``event``."""
        if event not in SUPPORTED_EVENTS:
            logger.warning("Unsupported webhook event: %s", event)
            return
        targets = [
            url for url, events in self._hooks.items() if event in events
        ]
        if not targets:
            return
        envelope = {"event": event, "payload": dict(payload) if payload else {}}
        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        if _has_aiohttp() and has_loop:
            asyncio.create_task(self._dispatch_aiohttp(targets, envelope))
        else:
            self._dispatch_thread(targets, envelope)

    async def _dispatch_aiohttp(self, urls: List[str], envelope: Dict[str, object]) -> None:
        import aiohttp

        if self._aiohttp_session is None:
            self._aiohttp_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Content-Type": "application/json"},
            )

        session = self._aiohttp_session
        body = json.dumps(envelope).encode("utf-8")
        for url in urls:
            try:
                async with session.post(url, data=body) as response:
                    if response.status >= 400:
                        logger.error(
                            "Webhook %s returned HTTP %s for event %s",
                            url,
                            response.status,
                            envelope.get("event"),
                        )
                    else:
                        logger.debug(
                            "Webhook %s OK for event %s", url, envelope.get("event")
                        )
            except Exception as exc:
                logger.error("Webhook %s failed for event %s: %s", url, envelope.get("event"), exc)

    def _dispatch_thread(self, urls: List[str], envelope: Dict[str, object]) -> None:
        def _post(url: str) -> None:
            try:
                data = json.dumps(envelope).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status >= 400:
                        logger.error(
                            "Webhook %s returned HTTP %s for event %s",
                            url,
                            response.status,
                            envelope.get("event"),
                        )
                    else:
                        logger.debug(
                            "Webhook %s OK for event %s", url, envelope.get("event")
                        )
            except Exception as exc:
                logger.error("Webhook %s failed for event %s: %s", url, envelope.get("event"), exc)

        for url in urls:
            threading.Thread(target=_post, args=(url,), daemon=True).start()

    async def close(self) -> None:
        """Close the shared aiohttp session, if any."""
        if self._aiohttp_session is not None:
            try:
                await self._aiohttp_session.close()
            except Exception as exc:
                logger.warning("Error closing webhook session: %s", exc)
            finally:
                self._aiohttp_session = None


# Global singleton instance used across the backend.
webhook_notifier = WebhookNotifier()


def _auto_register_from_env() -> None:
    env_url = os.environ.get("WEBHOOK_URL") or os.environ.get("GHOSTBROWSER_WEBHOOK_URL")
    if env_url:
        webhook_notifier.register(env_url, list(SUPPORTED_EVENTS))


_auto_register_from_env()
