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

## Specialist routing
- Claude definitions live in `.claude/agents/`: builders `zylebot-frontend`, `zylebot-backend`, `zylebot-db`; checkers `code-reviewer`, `verifier`.
- Codex definitions live in `.codex/agents/`: builders `frontend`, `backend`, `database`; checkers `reviewer`, `verifier`.
- **Specialists are opt-in only.** Never spawn a subagent unless Nemanja explicitly asks for an agent, delegation, or parallel work in the current request. Otherwise the main model handles the task directly. When delegation is explicitly requested, the orchestrator chooses the relevant roles, coordinates cross-domain work, and integrates their results.

## Ground rules
- Config: default in `app/config.py`, override in `.env`; keep `.env` and `.env.example` in sync.
- **Never run git commands** — no add/commit/push/branch/anything; Nemanja performs all git himself. Read-only `git status`/`git diff`/`git log` only when a task depends on it (e.g. the diff-scoped code-reviewer agent). The repo is private.
- `HANDOFF.md` is updated by the **main (orchestrator) model** — **every time work lands, end of every completed task, no exceptions** — so it's always known what's done. Subagents report, they don't document. Keep it current-state and small (not a changelog — git history is the changelog).
- `run_command` and other write/exec tools are `CONFIRM_REQUIRED` — keep the human approval gate. Do not help build weapons/explosives (the copyright/cutoff prompt nudge does not override real harm refusals).
