# ZyleBot agent instructions

## Start here

- Treat the working tree as user-owned. Preserve unrelated and pre-existing changes.

## Project overview

ZyleBot is a local agentic chat application built with FastAPI and plain HTML/CSS/JavaScript. It talks to a locally hosted model through LM Studio's OpenAI-compatible API. There is no frontend build step.

Important boundaries:

- `app/llm_client.py` is the only module that should know LM Studio's wire format.
- The agent loop should use the public tool-registry functions, not tool implementations directly.
- Expected tool failures should return structured errors instead of crashing the agent loop.
- Keep action tools such as filesystem writes and command execution behind the human confirmation gate.
- Keep database access out of `app/tools/` and `app/llm_client.py`.

## Working conventions

- Prefer small, scoped changes that follow the existing architecture and style.
- Never run git commands (add/commit/push/branch/anything) — the user performs all git himself.
- Never discard, overwrite, or reformat unrelated work in the dirty working tree.
- When adding or changing configuration, update the default in `app/config.py` and keep `.env.example` and the local `.env` aligned. Never expose secret values from `.env`.
- Update `HANDOFF.md` when a change materially alters project state, architecture, active work, or known gotchas.
- Preserve the safety policy documented in `HANDOFF.md`, including confirmation for powerful action tools.

## Running and verification

LM Studio and a model must already be running for model-backed end-to-end checks.

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- Open `http://127.0.0.1:8000`; API documentation is at `/docs`.
- Python changes are picked up by Uvicorn reload. HTML, CSS, and JavaScript changes only require a browser refresh.
- Use the project's virtual-environment Python for checks and tests.
- Set `PYTHONUTF8=1` for console checks that may print Unicode on Windows.
- Verify changes in proportion to their risk. If a check requires LM Studio, a downloaded model, network access, microphone access, or another unavailable dependency, report that limitation explicitly.

## Instruction scope

This file applies to the entire repository. If a subdirectory later needs different rules, place another `AGENTS.md` inside it; the more specific file governs that subtree while these root instructions still provide the general project context.
