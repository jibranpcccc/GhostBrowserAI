import os

class AILeakScanner:
    """
    Scans a newly created or about-to-launch profile for data leaks.
    """
    
    async def scan(self, profile: dict) -> dict:
        score = 100
        issues = []
        
        path = profile.get("path")
        if not path or not os.path.exists(path):
            return {"score": 0, "issues": ["Profile path does not exist"], "passed": False}
            
        # 1. Physical Artifact Leak Check
        default_dir = os.path.join(path, "Default")
        if os.path.exists(default_dir):
            targets = ["Cookies", "Local Storage", "IndexedDB", "Web Data"]
            found = []
            for t in targets:
                if os.path.exists(os.path.join(default_dir, t)):
                    found.append(t)
            
            if found:
                # Fresh profiles should not have these populated yet until actually browsed
                # But Playwright sometimes creates empty ones. If they have significant size, it's a leak.
                for t in found:
                    t_path = os.path.join(default_dir, t)
                    size = sum(os.path.getsize(os.path.join(dirpath, filename)) for dirpath, _, filenames in os.walk(t_path) for filename in filenames) if os.path.isdir(t_path) else os.path.getsize(t_path)
                    
                    if size > 1024 * 100: # > 100KB is suspicious for a brand new profile
                        score -= 20
                        issues.append(f"Suspicious physical artifact found: {t} (Size: {size} bytes)")
                        
        # 2. Automation Flag Check (Done during browser boot, but we can check the config here)
        advanced = profile.get("advanced", {})
        if advanced.get("disable_automation", True) is False:
            score -= 50
            issues.append("Automation prevention is disabled in advanced settings.")
            
        # 3. Simulated Runtime JavaScript Leak Check
        import asyncio
        from playwright.async_api import async_playwright
        import playwright_stealth
        
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=path,
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
                
                # If we injected advanced properties, we should apply our script here to test it
                advanced = profile.get("advanced", {})
                cpu_cores = advanced.get("cpu_cores") or 4
                memory_gb = advanced.get("memory_gb") or 8
                
                spoofing_script = f"""
                    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {cpu_cores} }});
                    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {memory_gb} }});
                    Object.defineProperty(navigator, 'webdriver', {{ get: () => false }});
                """
                await page.add_init_script(spoofing_script)
                await page.reload() # Reload to ensure init scripts take effect
                
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
                    
                # Check timing precision (should be coarse in modern browsers)
                timing = await page.evaluate("performance.now()")
                if not isinstance(timing, (int, float)):
                    score -= 10
                    issues.append("RUNTIME LEAK: performance.now() is abnormal")
                    
                # Generate automated Leak Test Report
                report = {
                    "pre_spoof": {"webdriver": pre_webdriver, "memory": pre_memory, "cores": pre_cores},
                    "post_spoof": {"webdriver": post_webdriver, "memory": post_memory, "cores": post_cores},
                    "issues": issues,
                    "final_score": score
                }
                
                # We could save this report to the profile dir
                import json
                report_path = os.path.join(path, "leak_test_report.json")
                with open(report_path, "w") as f:
                    json.dump(report, f, indent=4)
                    
                await context.close()
        except Exception as e:
            # If Playwright fails to launch, it's a critical failure for this profile
            score = 0
            issues.append(f"Simulated boot failed: {e}")
            
        return {
            "score": score,
            "issues": issues,
            "passed": score >= 95
        }

leak_scanner = AILeakScanner()
