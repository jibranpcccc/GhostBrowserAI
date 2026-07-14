import sys
import os
import uvicorn
import asyncio
import multiprocessing
import threading
import webbrowser
import time

# PyInstaller multiprocessing support
if sys.platform.startswith('win'):
    multiprocessing.freeze_support()

# Add the current directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.main import app

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    print("==============================================")
    print("  GhostBrowser API Server (Executable Mode)   ")
    print("==============================================")
    print("\nStarting server... Keep this window open!")
    print("Once started, you can navigate to http://127.0.0.1:8888\n")
    
    # Auto-install Playwright browsers for end-users so they don't have to install anything
    print("[System] Checking/Installing necessary browser binaries... (This may take a minute on first run)")
    import subprocess
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("[System] Browser binaries are ready.")
    except Exception as e:
        print(f"[System] Warning: Could not auto-install browser binaries: {e}")
    
    def open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:8888")
        
    threading.Thread(target=open_browser, daemon=True).start()
    
    uvicorn.run(app, host="127.0.0.1", port=8888)
