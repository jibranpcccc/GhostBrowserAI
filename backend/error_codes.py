"""
Central public error-code catalog.

Single source of truth for the stable codes and messages that may be surfaced
to API clients (A05). Any code not listed here must be collapsed by callers
into an operation default before it reaches an HTTP response so internal
exception details never leak.
"""

PUBLIC_CODE_MESSAGES = {
    "PIN_REQUIRED": "PIN required to launch this profile",
    "PIN_INVALID": "Incorrect PIN",
    "NOT_FOUND": "Profile not found",
    "KIMI_UNAVAILABLE": "Strict AI fingerprint service unavailable",
    "KIMI_TIMEOUT": "AI fingerprint generation timed out",
    "CHROMIUM_VERSION_MISSING": "Cannot determine installed Chromium version",
    "CREATE_FAILED": "Profile creation failed",
    "LAUNCH_FAILED": "Launch failed",
    "CLOSE_FAILED": "Close failed",
    "DELETE_FAILED": "Delete failed",
    "ACTION_FAILED": "Action failed",
}


def is_public_code(code: str) -> bool:
    """True only for codes that are safe to surface verbatim to clients."""
    return code in PUBLIC_CODE_MESSAGES


def public_message_for(code: str, default: str) -> str:
    """Canonical message for a stable public code, else ``default``."""
    return PUBLIC_CODE_MESSAGES.get(code, default)
