# AGENTS.md

## Project
GhostBrowserAI — AI-powered anti-detect browser. 100/100 audit score.

## Quick Start
- `cd C:\Users\jibra\Desktop\NEW DETECTOR`
- Server runs on `127.0.0.1:8888`
- Read `WORK_SUMMARY.md` for full context on everything done

## Key Facts
- Backend: `backend/main.py` (FastAPI, port 8888)
- Frontend: `frontend/index.html` + `app.js` + `style.css`
- Stealth: `backend/browser_manager.py` (1695 lines, 13+ sections)
- AI: `backend/ai_generator.py` (5-model chain, primary: GLM-4.7-Flash)
- Tests: `anti_detect_browser_test/run_tests.py` (ADMIN_TOKEN auth, port 8888)
- Secrets: `cloudflare_accounts.txt` (gitignored)
