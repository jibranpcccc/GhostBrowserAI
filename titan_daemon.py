import asyncio
import time
import os
import sys

# Add backend directory to path so we can import db
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
import db
from proxy_titan import ProxyTitan, CONCURRENCY_LIMIT

MIN_POOL_SIZE = 100
HEALTH_CHECK_INTERVAL_SECONDS = 900  # 15 minutes

async def daemon_loop():
    print("🛡️ Titan Proxy Daemon Initialized. Running continuous health checks & discovery.")
    
    while True:
        alive_proxies = db.get_all_alive_proxies()
        print(f"\n[Daemon] Current Active Pool Size: {len(alive_proxies)}")
        
        titan = ProxyTitan()
        
        # 1. Health Check Phase
        if alive_proxies:
            print("[Daemon] Commencing Health Check on existing pool...")
            # We add known alive proxies to the queue to re-check
            for p in alive_proxies:
                proxy_str = f"{p['ip']}:{p['port']}"
                titan.raw_proxies[proxy_str] = p['protocol']
            
            await titan.validate_all()
        
        # 2. Re-eval Pool Size
        alive_proxies = db.get_all_alive_proxies()
        print(f"[Daemon] Post-Health Check Pool Size: {len(alive_proxies)}")
        
        # 3. Discovery Phase
        if len(alive_proxies) < MIN_POOL_SIZE:
            print(f"[Daemon] Pool size below {MIN_POOL_SIZE}. Triggering full discovery scrape...")
            titan = ProxyTitan() # reset
            await titan.scrape_all()
            await titan.validate_all()
        else:
            print("[Daemon] Pool size is healthy. No discovery needed.")
            
        # 4. Sleep
        print(f"[Daemon] Sleeping for {HEALTH_CHECK_INTERVAL_SECONDS // 60} minutes...\n")
        await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(daemon_loop())
    except KeyboardInterrupt:
        print("\n[Daemon] Shutting down Titan Proxy Daemon.")
