import sys
import os
import uvicorn
import asyncio
import multiprocessing
import threading
import webbrowser
import time

if sys.platform.startswith('win'):
    multiprocessing.freeze_support()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.main import app

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print("=" * 50)
    print("  GhostBrowser AI - Anti-Detect Browser")
    print("=" * 50)
    print()
    print("Starting server...")
    print("Open http://127.0.0.1:8888 in your browser")
    print("Keep this window open while using GhostBrowser")
    print()

    print("[Setup] Checking browser engine...")
    import subprocess
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, capture_output=True
        )
        print("[Setup] Browser engine ready.")
    except Exception as e:
        print(f"[Setup] Warning: {e}")

    def open_browser():
        time.sleep(2)
        webbrowser.open("http://127.0.0.1:8888")

    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=8888)
