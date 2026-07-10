# PATCH: Register 5 new API routers in backend/main.py
#
# Add the following lines to main.py. There are two sections:
#   (A) Import lines — add after the existing imports (around line 22, after `from backend.system_monitor import system_monitor`)
#   (B) Router registration — add BEFORE the frontend mount (around line 646, before `frontend_dir = get_bundled_dir("frontend")`)
#
# This file is for reference. Apply manually or use the patch tool.

## ==========================================================================
## (A) IMPORTS — Add after line 22 (after `from backend.system_monitor import system_monitor`)
## ==========================================================================

from backend.api_automation import router as api_automation_router
from backend.synchronizer import router as synchronizer_router
from backend.rpa_recorder import router as rpa_recorder_router
from backend.profile_folders import router as profile_folders_router
from backend.bulk_operations import router as bulk_operations_router

## ==========================================================================
## (B) ROUTER REGISTRATION — Add before line 646 (before `frontend_dir = ...`)
## ==========================================================================

# --- New API Routers (Agent 1: Backend New API Endpoints & Automation) ---
app.include_router(api_automation_router)
app.include_router(synchronizer_router)
app.include_router(rpa_recorder_router)
app.include_router(profile_folders_router)
app.include_router(bulk_operations_router)

## ==========================================================================
## SUMMARY OF NEW ENDPOINTS
## ==========================================================================
##
## api_automation.py:
##   POST   /api/profiles/{id}/connect   → ws:// URL for Selenium/Puppeteer
##   GET    /api/api-keys                → list keys
##   POST   /api/api-keys                → generate new key
##   DELETE /api/api-keys/{key}          → revoke key
##
## synchronizer.py:
##   POST   /api/sync/start              → start sync session
##   POST   /api/sync/stop               → stop sync session
##   POST   /api/sync/action             → broadcast action to synced profiles
##   GET    /api/sync/status             → sync session status
##
## rpa_recorder.py:
##   POST   /api/rpa/record/start        → start recording
##   POST   /api/rpa/record/stop         → stop, returns steps
##   POST   /api/rpa/replay              → replay on target profiles
##   GET    /api/rpa/status              → recording status
##
## profile_folders.py:
##   GET    /api/folders                  → list folders
##   POST   /api/folders                  → create folder
##   DELETE /api/folders/{id}             → delete folder
##   PUT    /api/profiles/{id}/folder     → assign profile to folder
##   GET    /api/folders/{id}/profiles    → list profiles in folder
##
## bulk_operations.py:
##   POST   /api/profiles/bulk/create           → create N profiles
##   POST   /api/profiles/bulk/launch           → launch multiple
##   POST   /api/profiles/bulk/close            → close multiple
##   POST   /api/profiles/bulk/delete           → delete multiple
##   POST   /api/profiles/bulk/assign-folder     → assign folder to multiple
