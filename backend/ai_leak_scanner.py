import os
import tempfile
import shutil

class AILeakScanner:
    """
    Scans a newly created or about-to-launch profile for data leaks.
    CRIT-05 FIX: Uses a TEMPORARY directory for the headless browser boot so we never
    write Playwright/Chromium browser state (cookies, localStorage, caches) into the
    real profile folder before the user ever opens it.
    """
    
    async def scan(self, profile: dict) -> dict:
        score = 100
        issues = []
        
        path = profile.get("path")
        if not path or not os.path.exists(path):
            return {"score": 0, "issues": ["Profile path does not exist"], "passed": False}
            
        # 1. Physical Artifact Leak Check (check REAL profile dir for pre-existing state)
        default_dir = os.path.join(path, "Default")
        if os.path.exists(default_dir):
            targets = ["Cookies", "Local Storage", "IndexedDB", "Web Data"]
            found = []
            for t in targets:
                if os.path.exists(os.path.join(default_dir, t)):
                    found.append(t)
            
            if found:
                for t in found:
                    t_path = os.path.join(default_dir, t)
                    size = sum(os.path.getsize(os.path.join(dirpath, filename)) for dirpath, _, filenames in os.walk(t_path) for filename in filenames) if os.path.isdir(t_path) else os.path.getsize(t_path)
                    
                    if size > 1024 * 100: # > 100KB is suspicious for a brand new profile
                        score -= 20
                        issues.append(f"Suspicious physical artifact found: {t} (Size: {size} bytes)")
                        
        # 2. Automation Flag Check
        advanced = profile.get("advanced", {})
        if advanced.get("disable_automation", True) is False:
            score -= 50
            issues.append("Automation prevention is disabled in advanced settings.")
            
        # 3. Simulated Runtime JavaScript Leak Check
        # CRIT-05 FIX: Launch against a TEMP dir, never the real profile path.
        # This prevents browser state contamination before the user's first launch.
        import asyncio
        from playwright.async_api import async_playwright
        import playwright_stealth
        
        temp_dir = tempfile.mkdtemp(prefix="ghostbrowser_scan_")
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=temp_dir,  # TEMP dir — discarded after scan
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                page = context.pages[0] if context.pages else await context.new_page()
                
                # --- PRE-SPOOF BASELINE CHECK ---
                pre_webdriver = await page.evaluate("navigator.webdriver !== undefined ? navigator.webdriver : false")
                pre_memory = await page.evaluate("navigator.deviceMemory || 8")
                pre_cores = await page.evaluate("navigator.hardwareConcurrency || 4")
                
                # Apply stealth
                stealth = playwright_stealth.stealth.Stealth()
                await stealth.apply_stealth_async(page)
                
                advanced = profile.get("advanced", {})
                # FIX: Ensure advanced is a dict (could be encrypted string if profile not fully loaded)
                if not isinstance(advanced, dict):
                    advanced = {}
                cpu_cores = advanced.get("cpu_cores") or 4
                memory_gb = advanced.get("memory_gb") or 8
                # FIX: Ensure values are ints (never None or string leaking into JS f-string)
                try:
                    cpu_cores = int(cpu_cores)
                except (TypeError, ValueError):
                    cpu_cores = 4
                try:
                    memory_gb = int(memory_gb)
                except (TypeError, ValueError):
                    memory_gb = 8
                
                spoofing_script = f"""
                    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {cpu_cores} }});
                    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {memory_gb} }});
                    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
                """
                await page.add_init_script(spoofing_script)
                await page.reload()
                
                # --- POST-SPOOF CHECK ---
                post_webdriver = await page.evaluate("navigator.webdriver")
                post_memory = await page.evaluate("navigator.deviceMemory")
                post_cores = await page.evaluate("navigator.hardwareConcurrency")
                
                if post_webdriver is True:
                    score -= 50
                    issues.append("RUNTIME LEAK: navigator.webdriver is true post-spoof")
                    
                if post_memory == pre_memory and pre_memory != memory_gb:
                    score -= 10
                    issues.append(f"RUNTIME LEAK: deviceMemory did not spoof (Host: {pre_memory}, Expected: {memory_gb})")
                    
                if post_cores == pre_cores and pre_cores != cpu_cores:
                    score -= 10
                    issues.append(f"RUNTIME LEAK: hardwareConcurrency did not spoof (Host: {pre_cores}, Expected: {cpu_cores})")
                    
                # Check for $cdc (chromedriver artifact)
                cdc = await page.evaluate("typeof $cdc_asdjflasutopfhvcZLmcfl_ === 'undefined'")
                if not cdc:
                    score -= 50
                    issues.append("RUNTIME LEAK: $cdc_ artifact found")
                    
                # Check timing precision
                timing = await page.evaluate("performance.now()")
                if not isinstance(timing, (int, float)):
                    score -= 10
                    issues.append("RUNTIME LEAK: performance.now() is abnormal")
                    
                report = {
                    "pre_spoof": {"webdriver": pre_webdriver, "memory": pre_memory, "cores": pre_cores},
                    "post_spoof": {"webdriver": post_webdriver, "memory": post_memory, "cores": post_cores},
                    "issues": issues,
                    "final_score": score
                }
                
                # Save report to the REAL profile directory (metadata only, no browser state)
                import json
                report_path = os.path.join(path, "leak_test_report.json")
                with open(report_path, "w") as f:
                    json.dump(report, f, indent=4)
                    
                await context.close()
        except Exception as e:
            score = 0
            issues.append(f"Simulated boot failed: {e}")
        finally:
            # Always clean up the temp dir — never leave browser state behind
            shutil.rmtree(temp_dir, ignore_errors=True)
            
        return {
            "score": score,
            "issues": issues,
            "passed": score >= 95
        }

leak_scanner = AILeakScanner()
