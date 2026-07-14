"""
GhostBrowser AI - Proxy Provider Integration
BrightData, Oxylabs, Smartproxy, and custom provider support.
"""
import os
import json
import time
import asyncio
import aiohttp
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.config import get_data_dir
from backend.logging_config import logger

PROVIDERS_FILE = os.path.join(get_data_dir("profiles_data"), "proxy_providers.json")
os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)

router = APIRouter(prefix="/api/proxy-providers", tags=["proxy-providers"])


PROVIDER_TEMPLATES = {
    "brightdata": {
        "name": "BrightData",
        "type": "brightdata",
        "host": "brd.superproxy.io",
        "port": 22225,
        "protocol": "http",
        "fields": ["username", "password", "zone"],
        "username_template": "brd-customer-{customer_id}-zone-{zone}-session-{session_id}",
        "test_url": "http://ip-api.com/json",
        "docs_url": "https://brightdata.com/documentation/api",
    },
    "oxylabs": {
        "name": "Oxylabs",
        "type": "oxylabs",
        "host": "pr.oxylabs.io",
        "port": 7777,
        "protocol": "http",
        "fields": ["username", "password"],
        "username_template": "customer-{customer_id}-session-{session_id}",
        "test_url": "http://ip-api.com/json",
        "docs_url": "https://docs.oxylabs.io/",
    },
    "smartproxy": {
        "name": "Smartproxy",
        "type": "smartproxy",
        "host": "gate.smartproxy.com",
        "port": 7000,
        "protocol": "http",
        "fields": ["username", "password"],
        "username_template": "user-{user_id}-session-{session_id}",
        "test_url": "http://ip-api.com/json",
        "docs_url": "https://docs.smartproxy.com/",
    },
    "custom": {
        "name": "Custom Provider",
        "type": "custom",
        "host": "",
        "port": 8080,
        "protocol": "http",
        "fields": ["host", "port", "username", "password", "protocol"],
        "test_url": "http://ip-api.com/json",
    },
}


class ProviderConfig:
    def __init__(self, data: dict):
        self.id = data.get("id", str(int(time.time() * 1000)))
        self.name = data.get("name", "Unnamed")
        self.type = data.get("type", "custom")
        self.host = data.get("host", "")
        self.port = data.get("port", 8080)
        self.protocol = data.get("protocol", "http")
        self.username = data.get("username", "")
        self.password = data.get("password", "")
        self.zone = data.get("zone", "")
        self.customer_id = data.get("customer_id", "")
        self.session_id = data.get("session_id", "")
        self.country = data.get("country", "us")
        self.city = data.get("city", "")
        self.state = data.get("state", "")
        self.session_type = data.get("session_type", "sequential")
        self.active = data.get("active", True)
        self.last_tested = data.get("last_tested")
        self.last_test_ok = data.get("last_test_ok")
        self.proxy_count = data.get("proxy_count", 0)
        self.created_at = data.get("created_at", datetime.now(timezone.utc).isoformat())

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "host": self.host, "port": self.port, "protocol": self.protocol,
            "username": self.username, "password": self.password,
            "zone": self.zone, "customer_id": self.customer_id,
            "session_id": self.session_id, "country": self.country,
            "city": self.city, "state": self.state,
            "session_type": self.session_type, "active": self.active,
            "last_tested": self.last_tested, "last_test_ok": self.last_test_ok,
            "proxy_count": self.proxy_count, "created_at": self.created_at,
        }

    def get_proxy_url(self) -> str:
        auth = ""
        if self.username:
            un = self.username
            if self.type == "brightdata" and self.zone:
                un = un.replace("{zone}", self.zone)
            if self.customer_id:
                un = un.replace("{customer_id}", self.customer_id)
            if self.session_id:
                un = un.replace("{session_id}", self.session_id)
            if self.session_type == "random":
                un = un.replace("session", "sec") if "session" in un else un
            auth = f"{un}:{self.password}@"
        return f"{self.protocol}://{auth}{self.host}:{self.port}"

    def to_test_dict(self) -> dict:
        return {
            "server": f"{self.protocol}://{self.host}:{self.port}",
            "username": self.username or None,
            "password": self.password or None,
        }


class ProxyProviderManager:
    def __init__(self):
        self._providers: Dict[str, ProviderConfig] = {}
        self._load()

    def _load(self):
        if os.path.exists(PROVIDERS_FILE):
            try:
                with open(PROVIDERS_FILE, "r") as f:
                    data = json.load(f)
                for pdata in data.get("providers", []):
                    pc = ProviderConfig(pdata)
                    self._providers[pc.id] = pc
            except Exception as e:
                logger.error(f"Failed to load proxy providers: {e}")

    def _save(self):
        try:
            data = {"providers": [p.to_dict() for p in self._providers.values()]}
            with open(PROVIDERS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save proxy providers: {e}")

    def list_providers(self) -> List[dict]:
        return [p.to_dict() for p in self._providers.values()]

    def get_provider(self, provider_id: str) -> Optional[ProviderConfig]:
        return self._providers.get(provider_id)

    def add_provider(self, data: dict) -> ProviderConfig:
        pc = ProviderConfig(data)
        self._providers[pc.id] = pc
        self._save()
        logger.info(f"Proxy provider added: {pc.name} ({pc.type})")
        return pc

    def update_provider(self, provider_id: str, data: dict) -> Optional[ProviderConfig]:
        pc = self._providers.get(provider_id)
        if not pc:
            return None
        for k, v in data.items():
            if hasattr(pc, k) and v is not None:
                setattr(pc, k, v)
        self._save()
        return pc

    def delete_provider(self, provider_id: str) -> bool:
        if provider_id in self._providers:
            del self._providers[provider_id]
            self._save()
            return True
        return False

    async def test_provider(self, provider_id: str) -> dict:
        pc = self._providers.get(provider_id)
        if not pc:
            return {"success": False, "error": "Provider not found"}

        proxy_url = pc.get_proxy_url()
        test_url = PROVIDER_TEMPLATES.get(pc.type, {}).get("test_url", "http://ip-api.com/json")

        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(test_url, proxy=proxy_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pc.last_tested = datetime.now(timezone.utc).isoformat()
                        pc.last_test_ok = True
                        self._save()
                        return {
                            "success": True,
                            "ip": data.get("query", "unknown"),
                            "country": data.get("country", ""),
                            "city": data.get("city", ""),
                            "timezone": data.get("timezone", ""),
                            "isp": data.get("isp", ""),
                        }
                    else:
                        pc.last_tested = datetime.now(timezone.utc).isoformat()
                        pc.last_test_ok = False
                        self._save()
                        return {"success": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            pc.last_tested = datetime.now(timezone.utc).isoformat()
            pc.last_test_ok = False
            self._save()
            return {"success": False, "error": str(e)}

    async def fetch_proxies_from_provider(self, provider_id: str, count: int = 10) -> dict:
        pc = self._providers.get(provider_id)
        if not pc:
            return {"success": False, "error": "Provider not found"}

        if pc.type == "brightdata":
            return await self._fetch_brightdata(pc, count)
        elif pc.type == "oxylabs":
            return await self._fetch_oxylabs(pc, count)
        elif pc.type == "smartproxy":
            return await self._fetch_smartproxy(pc, count)
        else:
            return {"success": False, "error": f"Auto-fetch not supported for {pc.type}"}

    async def _fetch_brightdata(self, pc: ProviderConfig, count: int) -> dict:
        try:
            url = f"https://api.brightdata.com/zone/proxy?zone={pc.zone}&count={count}&format=json"
            headers = {"Authorization": f"Bearer {pc.password}"}
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        proxies = []
                        for p in data.get("proxies", data if isinstance(data, list) else []):
                            if isinstance(p, dict):
                                proxies.append(p)
                            elif isinstance(p, str):
                                parts = p.split(":")
                                if len(parts) >= 2:
                                    proxies.append({"ip": parts[0], "port": parts[1]})
                        pc.proxy_count = len(proxies)
                        self._save()
                        return {"success": True, "proxies": proxies, "count": len(proxies)}
                    return {"success": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _fetch_oxylabs(self, pc: ProviderConfig, count: int) -> dict:
        try:
            url = f"https://api.oxylabs.io/v1/proxy?limit={count}"
            auth = aiohttp.BasicAuth(pc.username, pc.password)
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, auth=auth) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        proxies = data.get("proxies", data.get("data", []))
                        pc.proxy_count = len(proxies)
                        self._save()
                        return {"success": True, "proxies": proxies, "count": len(proxies)}
                    return {"success": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _fetch_smartproxy(self, pc: ProviderConfig, count: int) -> dict:
        try:
            url = f"https://dashboard.smartproxy.com/api/proxies?count={count}"
            headers = {"Authorization": f"Bearer {pc.password}"}
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        proxies = data.get("proxies", [])
                        pc.proxy_count = len(proxies)
                        self._save()
                        return {"success": True, "proxies": proxies, "count": len(proxies)}
                    return {"success": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


provider_manager = ProxyProviderManager()


# --- Pydantic models ---
class ProviderModel(BaseModel):
    name: str
    type: str = "custom"
    host: str = ""
    port: int = 8080
    protocol: str = "http"
    username: str = ""
    password: str = ""
    zone: str = ""
    customer_id: str = ""
    session_id: str = ""
    country: str = "us"
    session_type: str = "sequential"


class ProviderUpdateModel(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    zone: Optional[str] = None
    country: Optional[str] = None
    active: Optional[bool] = None


@router.get("/templates")
def get_templates():
    return PROVIDER_TEMPLATES


@router.get("")
def list_providers():
    return provider_manager.list_providers()


@router.post("")
def add_provider(payload: ProviderModel):
    return provider_manager.add_provider(payload.dict()).to_dict()


@router.put("/{provider_id}")
def update_provider(provider_id: str, payload: ProviderUpdateModel):
    kwargs = {k: v for k, v in payload.dict().items() if v is not None}
    updated = provider_manager.update_provider(provider_id, kwargs)
    if not updated:
        raise HTTPException(status_code=404, detail="Provider not found")
    return updated.to_dict()


@router.delete("/{provider_id}")
def delete_provider(provider_id: str):
    if not provider_manager.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"status": "deleted"}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str):
    return await provider_manager.test_provider(provider_id)


@router.post("/{provider_id}/fetch")
async def fetch_proxies(provider_id: str, count: int = 10):
    return await provider_manager.fetch_proxies_from_provider(provider_id, count)
