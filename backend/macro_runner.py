import asyncio
import random
from typing import List, Dict, Any
from backend.logging_config import logger

class MacroRunner:
    @staticmethod
    async def run_macro(profile_id: str, macro: dict):
        from backend.browser_manager import launch_profile, close_profile, active_browsers
        
        is_running = profile_id in active_browsers
        if not is_running:
            res = await launch_profile(profile_id, force_headless=True)
            if res.get("status") == "error":
                return {"status": "error", "message": f"Failed to launch: {res.get('message')}"}
                
        try:
            browser_data = active_browsers[profile_id]
            page = browser_data["page"]
            
            steps = macro.get("steps", [])
            for i, step in enumerate(steps):
                action = step.get("action")
                logger.info(f"[Macro {profile_id}] Step {i+1}: {action}", extra={"profile_id": profile_id})
                
                try:
                    if action == "goto":
                        url = step.get("url")
                        if url:
                            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    
                    elif action == "wait":
                        ms = step.get("ms", 1000)
                        await page.wait_for_timeout(ms)
                        
                    elif action == "click":
                        selector = step.get("selector")
                        if selector:
                            await page.wait_for_selector(selector, state="visible", timeout=10000)
                            await page.click(selector)
                            
                    elif action == "type":
                        selector = step.get("selector")
                        text = step.get("text", "")
                        if selector:
                            await page.wait_for_selector(selector, state="visible", timeout=10000)
                            # Type like a human
                            await page.type(selector, text, delay=random.randint(50, 150))
                            
                    elif action == "scroll":
                        direction = step.get("direction", "down")
                        amount = step.get("amount", 500)
                        if direction == "down":
                            await page.mouse.wheel(0, amount)
                        else:
                            await page.mouse.wheel(0, -amount)
                        await page.wait_for_timeout(random.randint(500, 1500))
                        
                except Exception as e:
                    logger.error(f"[Macro {profile_id}] Step {i+1} Failed: {str(e)}", extra={"profile_id": profile_id})
                    return {"status": "error", "message": f"Step {i+1} ({action}) failed: {str(e)}"}
                    
            return {"status": "success", "message": "Macro completed successfully"}
            
        finally:
            if not is_running:
                await close_profile(profile_id)

    @staticmethod
    async def run_macro_bulk(profile_ids: List[str], macro: dict):
        # We will run them concurrently
        async def run_single(pid):
            res = await MacroRunner.run_macro(pid, macro)
            return {"profile_id": pid, "result": res}
            
        tasks = [run_single(pid) for pid in profile_ids]
        results = await asyncio.gather(*tasks)
        return results
