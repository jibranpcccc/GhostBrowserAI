import sys
import os

def get_base_dir():
    """
    Returns the absolute path to the root directory of the application.
    If running as a PyInstaller executable, returns the directory containing the .exe.
    If running as a Python script, returns the directory containing 'backend', 'frontend', etc.
    """
    if getattr(sys, 'frozen', False):
        # Running in a PyInstaller bundle
        return os.path.dirname(sys.executable)
    else:
        # Running in normal Python environment (one level up from backend)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def get_data_dir(*paths):
    """
    Returns an absolute path relative to the base user data directory.
    Creates the directory if it doesn't exist (unless it has an extension).
    """
    base = get_base_dir()
    full_path = os.path.join(base, *paths)
    
    # If the path looks like a directory (no extension), ensure it exists
    if not os.path.splitext(full_path)[1]:
        os.makedirs(full_path, exist_ok=True)
        
    return full_path

def get_bundled_dir(*paths):
    """
    Returns an absolute path to read-only bundled assets (like frontend html, default extensions).
    In PyInstaller, these extract to sys._MEIPASS.
    """
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, *paths)
