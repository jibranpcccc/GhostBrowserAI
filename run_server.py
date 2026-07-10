import sys
import os
import uvicorn
import asyncio
import multiprocessing

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
    print("Once started, you can navigate to http://127.0.0.1:8000\n")
    
    uvicorn.run(app, host="127.0.0.1", port=8000)
