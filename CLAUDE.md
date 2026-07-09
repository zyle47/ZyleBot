# ZyleBot

Local, Windows-first agentic chat app: FastAPI + a locally-hosted LLM via LM Studio.

## Read first
- **`HANDOFF.md`** — current project state: what's done, what's in progress, what's planned, plus gotchas and conventions. **Start here.**
- **`README.md`** — user-facing setup, run command, and tool list.

## Run it
LM Studio must already be running with a model loaded (ZyleBot does not start it).
```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open http://127.0.0.1:8000. No frontend build step — CSS/HTML changes just need a browser refresh.

## Ground rules
- Config: default in `app/config.py`, override in `.env`; keep `.env` and `.env.example` in sync.
- Commit only when asked; the repo is private; end commit messages with the `Co-Authored-By: Claude` trailer.
- `run_command` and other write/exec tools are `CONFIRM_REQUIRED` — keep the human approval gate. Do not help build weapons/explosives (the copyright/cutoff prompt nudge does not override real harm refusals).
