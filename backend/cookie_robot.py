"""
CookieRobot — Smart geo-targeted cookie warming robot.

Features:
- Geo-targeted site selection (US sites for US proxy, etc.)
- Visit 10-20 relevant sites per profile
- Collect cookies, local storage, build browsing history
- Random dwell time, scroll depth, mouse movements
- Tracks warming status per profile

API endpoints (registered in main.py):
  POST /api/cookie-robot/start          → start warming for profile(s)
  GET  /api/cookie-robot/status/{id}    → warming progress for a profile
  GET  /api/cookie-robot/status         → warming status for all profiles
"""

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from typing import Dict, List, Any

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "profiles_data")

# ---------------------------------------------------------------------------
# Geo-targeted site catalog
# ---------------------------------------------------------------------------

GEO_SITES: Dict[str, List[dict]] = {
    "US": [
        {"url": "https://www.google.com",         "category": "search",    "weight": 10},
        {"url": "https://www.amazon.com",         "category": "shopping",  "weight": 9},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 8},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 8},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 9},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 6},
        {"url": "https://www.yahoo.com",          "category": "news",      "weight": 6},
        {"url": "https://www.microsoft.com",     "category": "tech",      "weight": 5},
        {"url": "https://www.quora.com",          "category": "social",    "weight": 4},
        {"url": "https://www.cnn.com",            "category": "news",      "weight": 5},
        {"url": "https://www.nytimes.com",        "category": "news",      "weight": 4},
        {"url": "https://www.ebay.com",           "category": "shopping",  "weight": 5},
        {"url": "https://www.weather.com",        "category": "utility",   "weight": 4},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 4},
        {"url": "https://www.github.com",         "category": "tech",      "weight": 4},
        {"url": "https://www.stackoverflow.com",  "category": "tech",      "weight": 3},
        {"url": "https://www.linkedin.com",       "category": "social",    "weight": 4},
        {"url": "https://www.paypal.com",         "category": "finance",   "weight": 3},
        {"url": "https://www.instagram.com",      "category": "social",    "weight": 5},
        {"url": "https://www.bbc.com",            "category": "news",      "weight": 3},
    ],
    "GB": [
        {"url": "https://www.google.co.uk",       "category": "search",    "weight": 10},
        {"url": "https://www.bbc.co.uk",          "category": "news",      "weight": 9},
        {"url": "https://www.amazon.co.uk",       "category": "shopping",  "weight": 8},
        {"url": "https://www.theguardian.com",    "category": "news",      "weight": 6},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 7},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 8},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 7},
        {"url": "https://www.bbc.com",            "category": "news",      "weight": 5},
        {"url": "https://www.ebay.co.uk",         "category": "shopping",  "weight": 5},
        {"url": "https://www.gov.uk",             "category": "utility",   "weight": 3},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 5},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 4},
        {"url": "https://www.linkedin.com",       "category": "social",    "weight": 4},
        {"url": "https://www.quora.com",          "category": "social",    "weight": 3},
        {"url": "https://www.weather.com",        "category": "utility",   "weight": 3},
    ],
    "DE": [
        {"url": "https://www.google.de",          "category": "search",    "weight": 10},
        {"url": "https://www.amazon.de",          "category": "shopping",  "weight": 8},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 7},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 8},
        {"url": "https://www.spiegel.de",         "category": "news",      "weight": 6},
        {"url": "https://www.bild.de",            "category": "news",      "weight": 5},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 5},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 5},
        {"url": "https://www.ebay.de",            "category": "shopping",  "weight": 4},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 3},
        {"url": "https://www.github.com",         "category": "tech",      "weight": 4},
        {"url": "https://www.linkedin.com",       "category": "social",    "weight": 4},
        {"url": "https://www.yahoo.com",          "category": "news",      "weight": 3},
        {"url": "https://www.microsoft.com",     "category": "tech",      "weight": 3},
    ],
    "FR": [
        {"url": "https://www.google.fr",          "category": "search",    "weight": 10},
        {"url": "https://www.amazon.fr",          "category": "shopping",  "weight": 8},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 7},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 8},
        {"url": "https://www.lemonde.fr",        "category": "news",      "weight": 6},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 5},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 5},
        {"url": "https://www.ebay.fr",            "category": "shopping",  "weight": 4},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 3},
        {"url": "https://www.github.com",         "category": "tech",      "weight": 3},
        {"url": "https://www.linkedin.com",       "category": "social",    "weight": 4},
        {"url": "https://www.yahoo.com",          "category": "news",      "weight": 3},
    ],
    "JP": [
        {"url": "https://www.google.co.jp",       "category": "search",    "weight": 10},
        {"url": "https://www.amazon.co.jp",       "category": "shopping",  "weight": 8},
        {"url": "https://www.yahoo.co.jp",        "category": "news",      "weight": 8},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 6},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 7},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 4},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 4},
        {"url": "https://www.github.com",         "category": "tech",      "weight": 3},
        {"url": "https://www.linkedin.com",       "category": "social",    "weight": 3},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 3},
        {"url": "https://www.microsoft.com",     "category": "tech",      "weight": 3},
    ],
    "CA": [
        {"url": "https://www.google.ca",          "category": "search",    "weight": 10},
        {"url": "https://www.amazon.ca",          "category": "shopping",  "weight": 8},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 7},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 7},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 8},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 5},
        {"url": "https://www.cbc.ca",             "category": "news",      "weight": 5},
        {"url": "https://www.yahoo.com",          "category": "news",      "weight": 4},
        {"url": "https://www.ebay.ca",            "category": "shopping",  "weight": 4},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 3},
        {"url": "https://www.github.com",         "category": "tech",      "weight": 3},
        {"url": "https://www.linkedin.com",       "category": "social",    "weight": 4},
    ],
    "AU": [
        {"url": "https://www.google.com.au",      "category": "search",    "weight": 10},
        {"url": "https://www.amazon.com.au",      "category": "shopping",  "weight": 7},
        {"url": "https://www.wikipedia.org",      "category": "reference", "weight": 7},
        {"url": "https://www.reddit.com",         "category": "social",    "weight": 6},
        {"url": "https://www.youtube.com",        "category": "video",     "weight": 8},
        {"url": "https://www.abc.net.au",         "category": "news",      "weight": 5},
        {"url": "https://www.bing.com",           "category": "search",    "weight": 5},
        {"url": "https://www.ebay.com.au",        "category": "shopping",  "weight": 4},
        {"url": "https://www.imdb.com",           "category": "entertainment", "weight": 3},
        {"url": "https://www.github.com",         "category": "tech",      "weight": 3},
    ],
}

# Fallback generic sites used when proxy country is unknown
DEFAULT_SITES = GEO_SITES["US"]

# Category search queries for search engines (builds realistic search history)
SEARCH_QUERIES = {
    "US": ["weather today", "best laptop 2024", "news today", "how to tie a tie", "recipe for pancakes"],
    "GB": ["weather today", "best laptop 2024", "news today", "train times", "recipe for pancakes"],
    "DE": ["wetter heute", "best laptop 2024", "nachrichten heute", "bahn verbindungen"],
    "FR": ["météo aujourd'hui", "meilleur ordinateur portable", "actualités aujourd'hui"],
    "JP": ["今日の天気", "ベストノートパソコン", "今日のニュース"],
    "CA": ["weather today", "best laptop 2024", "news today"],
    "AU": ["weather today", "best laptop 2024", "news today"],
}


class CookieRobot:
    """
    Smart cookie warming robot.

    Geo-targets site selection based on the profile's proxy country,
    visits 10-20 relevant sites, simulates human browsing behavior,
    and collects cookies + localStorage to build a realistic history.
    """

    def __init__(self):
        # Per-profile status tracking
        # status dict shape:
        # {
        #   "state": "idle" | "warming" | "completed" | "failed",
        #   "profile_id": str,
        #   "country": str,
        #   "sites_visited": int,
        #   "sites_total": int,
        #   "cookies_collected": int,
        #   "current_site": str,
        #   "started_at": iso str,
        #   "finished_at": iso str | None,
        #   "error": str | None,
        #   "history": [{"url", "category", "dwell_time_sec", "timestamp"}],
        # }
        self.status: Dict[str, dict] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_warming(
        self,
        profile_ids: List[str],
        min_sites: int = 10,
        max_sites: int = 20,
    ) -> Dict[str, Any]:
        """Start warming for one or more profiles.  Returns immediately."""
        _test_env = os.environ.get("GHOSTBROWSER_TEST_ENV", "").strip().lower() in ("1", "true")
        if _test_env:
            return {
                "status": "success",
                "started": [],
                "skipped": [],
                "message": "Cookie warming disabled in test environment.",
            }

        started = []
        skipped = []

        for pid in profile_ids:
            existing = self.status.get(pid, {})
            if existing.get("state") == "warming":
                skipped.append(pid)
                continue

            task = asyncio.create_task(self.run(pid, min_sites, max_sites))
            self._tasks[pid] = task
            started.append(pid)

        return {
            "status": "success",
            "started": started,
            "skipped": skipped,
            "message": f"Started cookie warming for {len(started)} profile(s).",
        }

    async def cancel_all(self):
        """Cancel all running warming tasks and wait for them to finish."""
        for pid, task in list(self._tasks.items()):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            self._tasks.pop(pid, None)

    def reset(self):
        """Reset all status and task state."""
        self.status.clear()
        self._tasks.clear()

    def get_status(self, profile_id: str) -> dict:
        """Return the warming status for a single profile."""
        return self.status.get(profile_id, {
            "state": "idle",
            "profile_id": profile_id,
            "message": "No warming has been performed for this profile.",
        })

    def get_all_status(self) -> Dict[str, dict]:
        """Return warming status for all known profiles."""
        return dict(self.status)

    async def stop_warming(self, profile_id: str) -> dict:
        """Cancel a running warming task for a profile."""
        task = self._tasks.get(profile_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.pop(profile_id, None)
        self.status[profile_id] = {
            "state": "stopped",
            "profile_id": profile_id,
            "message": "Warming was manually stopped.",
        }
        return self.status[profile_id]

    # ------------------------------------------------------------------
    # Core warming logic
    # ------------------------------------------------------------------

    async def run(self, profile_id: str, min_sites: int = 10, max_sites: int = 20):
        """
        Main warming routine for a single profile.

        1. Resolve proxy country from the profile
        2. Select geo-appropriate sites (weighted, 10-20)
        3. Launch browser headless
        4. Visit each site with human-like behavior
        5. Collect cookies + localStorage
        6. Close browser and mark complete
        """
        from backend.browser_manager import launch_profile, close_profile, active_browsers
        from backend.profile_manager import profile_manager

        # --- Resolve geo ---
        profile = profile_manager.get_profile(profile_id)
        if not profile:
            self._set_status(profile_id, state="failed", error=f"Profile {profile_id} not found")
            self._tasks.pop(profile_id, None)
            return False

        country = self._detect_country(profile)
        sites_pool = GEO_SITES.get(country, DEFAULT_SITES)
        num_sites = random.randint(min(min_sites, len(sites_pool)), min(max_sites, len(sites_pool)))

        # Weighted selection without replacement
        selected_sites = self._weighted_sample(sites_pool, num_sites)

        # --- Initialize status ---
        self._set_status(profile_id, state="warming", country=country,
                         sites_visited=0, sites_total=num_sites,
                         cookies_collected=0, current_site="",
                         started_at=datetime.now(timezone.utc).isoformat(),
                         finished_at=None, error=None, history=[])

        print(f"[CookieRobot] 🤖 Starting warming for {profile_id} (country={country}, sites={num_sites})")

        # --- Launch browser ---
        try:
            page_data = await launch_profile(profile_id, force_headless=True)
            if page_data.get("status") == "error":
                self._set_status(profile_id, state="failed",
                                 error=f"Launch failed: {page_data.get('message', '')}")
                print(f"[CookieRobot] ❌ Launch failed for {profile_id}: {page_data}")
                self._tasks.pop(profile_id, None)
                return False

            # Wait for browser to register
            await asyncio.sleep(1)
            if profile_id not in active_browsers:
                self._set_status(profile_id, state="failed",
                                 error="Browser did not register in active_browsers")
                self._tasks.pop(profile_id, None)
                return False

            page = active_browsers[profile_id]["page"]
            warm_start = time.time()

        except Exception as e:
            self._set_status(profile_id, state="failed", error="Launch exception")
            print(f"[CookieRobot] ❌ Exception launching {profile_id}: {e}")
            self._tasks.pop(profile_id, None)
            return False

        # --- Visit sites ---
        cookies_total = 0
        for idx, site in enumerate(selected_sites):
            url = site["url"]
            category = site.get("category", "unknown")

            # Update status
            self._update_status(profile_id, current_site=url)

            dwell_start = time.time()
            success = False
            try:
                print(f"[CookieRobot] ({idx+1}/{num_sites}) Visiting {url}")
                await page.goto(url, timeout=25000, wait_until="domcontentloaded")

                # --- Human behavior simulation ---
                await self._simulate_browsing(page, country)
                await self._age_page(page)

                # --- Collect cookies & localStorage ---
                try:
                    cookies = await page.context.cookies()
                    cookies_total += len(cookies)
                except Exception:
                    pass

                try:
                    await page.evaluate("""() => {
                        // Persist localStorage as a side-effect — many sites set items
                        try { localStorage.setItem('_gb_ts', Date.now().toString()); } catch(e) {}
                    }""")
                except Exception:
                    pass

                # Occasionally perform a search on search engines
                if category == "search" and random.random() < 0.6:
                    await self._perform_search(page, country)

                success = True

            except Exception as e:
                print(f"[CookieRobot] ⚠️ Failed to visit {url}: {e}")

            dwell_time = round(time.time() - dwell_start, 2)

            # Record history
            self._add_history(profile_id, {
                "url": url,
                "category": category,
                "success": success,
                "dwell_time_sec": dwell_time,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self._update_status(profile_id,
                                sites_visited=idx + 1,
                                cookies_collected=cookies_total)

        elapsed_minutes = round((time.time() - warm_start) / 60.0, 2) if 'warm_start' in dir() else 0.0
        self._bump_age_state(profile_id, elapsed_minutes, num_sites)

        # --- Close browser ---
        try:
            await close_profile(profile_id)
        except Exception as e:
            print(f"[CookieRobot] ⚠️ Error closing profile {profile_id}: {e}")

        # --- Finalize ---
        self._set_status(profile_id, state="completed",
                         current_site="",
                         finished_at=datetime.now(timezone.utc).isoformat(),
                         cookies_collected=cookies_total)
        print(f"[CookieRobot] ✅ Completed warming for {profile_id} "
              f"({num_sites} sites, {cookies_total} cookies)")

        # Clean up task ref
        self._tasks.pop(profile_id, None)
        return True

    # ------------------------------------------------------------------
    # Human behavior simulation (richer than cookie_warmer.py)
    # ------------------------------------------------------------------

    async def _simulate_browsing(self, page, country: str):
        """Simulate realistic browsing on a loaded page."""
        # Initial reading pause
        await asyncio.sleep(random.uniform(1.5, 4.0))

        # Random scroll pattern — sometimes read top, sometimes jump around
        scroll_pattern = random.choice(["linear", "jump", "reverse"])

        if scroll_pattern == "linear":
            # Scroll progressively down
            scrolls = random.randint(2, 6)
            for _ in range(scrolls):
                amt = random.randint(200, 700)
                await page.evaluate(f"window.scrollBy(0, {amt})")
                await asyncio.sleep(random.uniform(0.5, 2.0))

        elif scroll_pattern == "jump":
            # Jump around — go down, up, back down
            await page.evaluate(f"window.scrollBy(0, {random.randint(500, 1000)})")
            await asyncio.sleep(random.uniform(0.8, 2.0))
            await page.evaluate(f"window.scrollBy(0, -{random.randint(200, 500)})")
            await asyncio.sleep(random.uniform(0.8, 2.0))
            await page.evaluate(f"window.scrollBy(0, {random.randint(300, 800)})")
            await asyncio.sleep(random.uniform(0.5, 1.5))

        elif scroll_pattern == "reverse":
            # Go down a lot, then back up slowly
            await page.evaluate(f"window.scrollBy(0, {random.randint(600, 1200)})")
            await asyncio.sleep(random.uniform(1.0, 3.0))
            for _ in range(random.randint(2, 4)):
                await page.evaluate(f"window.scrollBy(0, -{random.randint(150, 400)})")
                await asyncio.sleep(random.uniform(0.5, 1.5))

        # Mouse movements — move to random points
        mouse_moves = random.randint(2, 5)
        for _ in range(mouse_moves):
            x = random.randint(50, 1200)
            y = random.randint(50, 800)
            try:
                await page.mouse.move(x, y, steps=random.randint(5, 15))
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.3, 1.2))

        # Occasionally hover over a link
        if random.random() < 0.4:
            try:
                links = await page.query_selector_all("a")
                if links:
                    link = random.choice(links[:20])  # first 20 links
                    await link.hover()
                    await asyncio.sleep(random.uniform(0.5, 2.0))
            except Exception:
                pass

        # Final dwell — realistic reading time
        await asyncio.sleep(random.uniform(1.0, 5.0))

    async def _perform_search(self, page, country: str):
        """Perform a search on a search engine to build realistic history."""
        queries = SEARCH_QUERIES.get(country, SEARCH_QUERIES["US"])
        query = random.choice(queries)

        try:
            # Try Google's search box
            search_input = await page.query_selector("input[name='q'], textarea[name='q'], input[type='search']")
            if search_input:
                await search_input.click()
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await page.keyboard.type(query, delay=random.randint(50, 150))
                await asyncio.sleep(random.uniform(0.3, 0.6))
                await page.keyboard.press("Enter")
                await asyncio.sleep(random.uniform(2.0, 4.0))

                # Scroll results
                await page.evaluate(f"window.scrollBy(0, {random.randint(200, 600)})")
                await asyncio.sleep(random.uniform(1.0, 2.5))
        except Exception as e:
            print(f"[CookieRobot] Search failed (non-critical): {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_country(self, profile: dict) -> str:
        """Try to determine the proxy country from the profile."""
        # Check if country is explicitly stored
        country = profile.get("country") or profile.get("geo_country")
        if country and country.upper() in GEO_SITES:
            return country.upper()

        # Try to infer from locale
        locale = profile.get("locale", "")
        if locale:
            parts = locale.split("-")
            if len(parts) >= 2:
                cc = parts[-1].upper()
                if cc in GEO_SITES:
                    return cc

        # Try to infer from timezone
        tz = profile.get("timezone", "")
        tz_map = {
            "America/New_York": "US", "America/Chicago": "US",
            "America/Los_Angeles": "US", "America/Denver": "US",
            "America/Toronto": "CA", "America/Vancouver": "CA",
            "Europe/London": "GB",
            "Europe/Berlin": "DE", "Europe/Paris": "FR",
            "Asia/Tokyo": "JP",
            "Australia/Sydney": "AU", "Australia/Melbourne": "AU",
        }
        if tz in tz_map:
            return tz_map[tz]

        # Check proxy string for country hints
        proxy = profile.get("proxy", {})
        proxy_str = ""
        if isinstance(proxy, dict):
            proxy_str = proxy.get("server", "")
        elif isinstance(proxy, str):
            proxy_str = proxy

        # Can't determine — default to US
        return "US"

    def _weighted_sample(self, sites: List[dict], k: int) -> List[dict]:
        """Weighted sampling without replacement."""
        pool = list(sites)
        weights = [s.get("weight", 5) for s in pool]
        selected = []

        for _ in range(min(k, len(pool))):
            total = sum(weights)
            if total <= 0:
                break
            r = random.uniform(0, total)
            cum = 0
            for i, w in enumerate(weights):
                cum += w
                if r <= cum:
                    selected.append(pool.pop(i))
                    weights.pop(i)
                    break

        return selected

    def _set_status(self, profile_id: str, **kwargs):
        """Initialize or overwrite status for a profile."""
        base = {
            "state": "idle",
            "profile_id": profile_id,
            "country": "",
            "sites_visited": 0,
            "sites_total": 0,
            "cookies_collected": 0,
            "current_site": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "history": [],
        }
        base.update(kwargs)
        self.status[profile_id] = base

    def _update_status(self, profile_id: str, **kwargs):
        """Merge updates into existing status."""
        if profile_id not in self.status:
            self._set_status(profile_id)
        self.status[profile_id].update(kwargs)

    def _add_history(self, profile_id: str, entry: dict):
        """Append a history entry."""
        if profile_id not in self.status:
            self._set_status(profile_id)
        self.status[profile_id].setdefault("history", []).append(entry)


    # ------------------------------------------------------------------
    # Lightweight profile aging
    # ------------------------------------------------------------------

    async def _age_page(self, page):
        """Tiny randomized background task: click an anchor, type in an input, or go back/forward.

        Falls back to a harmless scroll + sleep when no anchors or inputs exist.
        """
        try:
            action = random.choice(["click", "type", "back", "scroll"])
            if action == "click":
                clicked = await page.evaluate("""() => {
                    const a = Array.from(document.querySelectorAll('a')).find(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0 && el.href
                            && !el.href.startsWith('javascript:')
                            && !el.href.startsWith('mailto:');
                    });
                    if (a) { a.click(); return true; }
                    return false;
                }""")
                if not clicked:
                    await self._age_scroll(page)
            elif action == "type":
                typed = await page.evaluate("""() => {
                    const input = Array.from(document.querySelectorAll('input, textarea')).find(el => {
                        const t = el.type;
                        return el.offsetWidth > 0 && el.offsetHeight > 0
                            && !['hidden','submit','button','checkbox','radio','file','image'].includes(t);
                    });
                    if (input) {
                        const words = ['news','weather','search','maps','shop','recipes','sports'];
                        input.value = words[Math.floor(Math.random() * words.length)];
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        return true;
                    }
                    return false;
                }""")
                if not typed:
                    await self._age_scroll(page)
            elif action == "back":
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=5000)
                except Exception:
                    await self._age_scroll(page)
            else:
                await self._age_scroll(page)
            await asyncio.sleep(random.uniform(0.4, 1.2))
        except Exception as e:
            print(f"[CookieRobot] Age task error: {e}")

    async def _age_scroll(self, page):
        try:
            await page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.4)")
        except Exception:
            pass

    def _age_state_path(self, profile_id: str) -> str:
        return os.path.join(PROFILES_DIR, profile_id, "age_state.json")

    def _load_age_state(self, profile_id: str) -> dict:
        path = self._age_state_path(profile_id)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return {
                            "total_warm_minutes": float(data.get("total_warm_minutes", 0)),
                            "sites_visited_count": int(data.get("sites_visited_count", 0)),
                            "last_warmed_at": data.get("last_warmed_at"),
                        }
            except Exception:
                pass
        return {
            "total_warm_minutes": 0.0,
            "sites_visited_count": 0,
            "last_warmed_at": None,
        }

    def _save_age_state(self, profile_id: str, state: dict) -> None:
        path = self._age_state_path(profile_id)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"[CookieRobot] Failed to save age state for {profile_id}: {e}")

    def _bump_age_state(self, profile_id: str, minutes: float, sites: int) -> None:
        state = self._load_age_state(profile_id)
        state["total_warm_minutes"] = round(state["total_warm_minutes"] + minutes, 2)
        state["sites_visited_count"] += sites
        state["last_warmed_at"] = datetime.now(timezone.utc).isoformat()
        self._save_age_state(profile_id, state)


# Singleton instance
cookie_robot = CookieRobot()
