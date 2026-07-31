"""
Backwards-compatible shim for the merged AI leak scanner.

The canonical implementation now lives in ``backend.ai_leak_scanner``
(``AILeakScanner``, singleton ``leak_scanner``). This module keeps the legacy
``backend.ai_scanner.ai_scanner`` import path working and exposes the same
singleton instance so there is exactly one scanner in the process.
"""
from backend.ai_leak_scanner import AILeakScanner, leak_scanner

ai_scanner = leak_scanner

__all__ = ["AILeakScanner", "ai_scanner"]
