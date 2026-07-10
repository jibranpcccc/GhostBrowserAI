from playwright.async_api import Page, Response
from backend.logging_config import logger
from backend.config import get_data_dir
import asyncio

QUARANTINE_DIR = get_data_dir("quarantined_profiles")

class AIAnomalyDetector:
    """
    Actively monitors running profiles for signs of bot detection,
    such as Cloudflare challenges, reCAPTCHA iframes, or 429 Too Many Requests.
    """
    def __init__(self):
        pass

    async def attach(self, profile_id: str, page: Page):
        """
        Attaches event listeners to the page.
        """
        # Listen to responses
        page.on("response", lambda response: asyncio.create_task(self._on_response(profile_id, response)))
        
        # We can also periodically scan the DOM for challenge iframes
        asyncio.create_task(self._monitor_dom_loop(profile_id, page))

    async def _on_response(self, profile_id: str, response: Response):
        try:
            status = response.status
            url = response.url
            
            if status == 429:
                logger.warning(f"Rate limited (429) on {url}", extra={
                    "profile_id": profile_id, 
                    "event_type": "anomaly_429",
                    "action_needed": "rotate_proxy"
                })
            
            if status == 403:
                # Cloudflare often returns 403 for blocks
                headers = await response.all_headers()
                if "cf-ray" in headers:
                    logger.warning(f"Cloudflare 403 Block on {url}", extra={
                        "profile_id": profile_id, 
                        "event_type": "anomaly_cf_block",
                        "action_needed": "evaluate_fingerprint_or_proxy"
                    })
                    
        except Exception:
            pass

    async def _monitor_dom_loop(self, profile_id: str, page: Page):
        """
        Periodically checks the DOM for common captcha selectors.
        """
        while not page.is_closed():
            try:
                # Check for Cloudflare Turnstile
                turnstile = await page.evaluate("() => document.querySelector('.cf-turnstile') !== null")
                if turnstile:
                    logger.warning("Cloudflare Turnstile challenge detected.", extra={
                        "profile_id": profile_id, 
                        "event_type": "anomaly_challenge",
                        "action_needed": "evaluate_behavioral_engine"
                    })
                    
                # Check for reCAPTCHA
                recaptcha = await page.evaluate("() => document.querySelector('iframe[src*=\"recaptcha/api2\"]') !== null")
                if recaptcha:
                    logger.warning("reCAPTCHA detected.", extra={
                        "profile_id": profile_id, 
                        "event_type": "anomaly_challenge",
                        "action_needed": "solve_captcha_or_rotate"
                    })
                    
            except Exception:
                # Page might be navigating or closed
                pass
                
            await asyncio.sleep(15) # Check every 15 seconds

anomaly_detector = AIAnomalyDetector()
