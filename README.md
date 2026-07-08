# ZyleBot

A local, Windows-only agentic chat assistant. It talks to a locally-hosted LLM via
[LM Studio](https://lmstudio.ai/) and can call tools to inspect your system, search the
web, read pages, check the weather, and — with your explicit approval — write files and
run PowerShell commands. Single-page web UI, per-conversation memory, everything runs on
your machine.

Built with FastAPI + a small multi-step ReAct agent loop. No cloud, no API keys required
(default web search uses DuckDuckGo; weather uses Open-Meteo — both keyless).

---

## Prerequisites

- **Windows, macOS, or Linux.** ZyleBot detects the OS and uses the native shell for
  `run_command` (PowerShell on Windows, `$SHELL`/bash on macOS/Linux); all other tools use
  cross-platform `pathlib`/`psutil`.
- **Python 3.14.**
- **LM Studio** installed, with at least one chat model downloaded. The default model is
  `qwythos-9b-claude-mythos-5-1m`; any tool-calling-capable model works (set it in `.env`).

## Setup

**Windows (PowerShell):**

```powershell
# from the project root (F:\local_mythos)
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # create your local config from the template
```

**macOS / Linux:**

```bash
python3.14 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` if you want (model name, your name, timeouts, etc.). All keys have sensible
defaults and are documented in `.env.example`.

## Start LM Studio's server

ZyleBot needs LM Studio's OpenAI-compatible server running with a model loaded:

1. Open LM Studio → **Developer / Local Server** tab → **Start Server** (default port `1234`).
2. Load your model. **Recommended load settings for a 12 GB GPU (e.g. RTX 3080 Ti):**
   - **GPU offload:** max (all layers)
   - **Context length:** `65536` (64k) — fits fully in 12 GB VRAM; higher spills into slow
     shared memory. Watch Task Manager → GPU → *Shared GPU memory* stays ~0.
   - **Flash Attention:** on

   (CLI equivalent: `lms server start`, then load via the app or `lms load <model>`.)

ZyleBot auto-detects the loaded context-window size and shows it in the header gauge.

## Run ZyleBot

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open **http://127.0.0.1:8000**. The raw API is browsable at **/docs**.

---

## Tools

**Read-only (run automatically):**

| Tool | Purpose |
|---|---|
| `list_directory` | List a folder's contents |
| `read_file` | Read a text file (paginated for long files) |
| `search_files` | Recursive glob search |
| `get_file_info` | File/folder metadata |
| `get_system_info` | OS / CPU / RAM / disk usage |
| `web_search` | Web search (DuckDuckGo, no key) |
| `fetch_url` | Download + extract a page's readable text (paginated via `offset`) |
| `get_weather` | Current weather by place name (Open-Meteo, no key) |

**Actions (require your approval in the UI — the `confirm_required` tier):**

| Tool | Purpose |
|---|---|
| `write_file` | Create/overwrite a text file |
| `append_file` | Append to a file |
| `make_directory` | Create a directory |
| `run_command` | Run a PowerShell command (full system access) |

When the model wants to run an action tool, the turn **pauses** and an Approve / Deny card
appears showing the exact tool and arguments. Nothing runs until you approve. Read-only
tools never prompt.

---

## How it works

- **Per-conversation memory.** Each chat is its own isolated brain, stored in SQLite
  (`data/zylebot.db`); history survives restarts. Starting a new chat = a blank brain.
  The only globally-shared fact is your name (`USER_NAME` in `.env`).
- **Stateless LLM.** LM Studio holds no conversation state — ZyleBot sends the relevant
  chat's full history on each request. The header gauge shows real token usage vs the
  loaded context window.
- **Multi-step agent loop.** The model can chain tool calls (search → fetch → answer),
  up to `AGENT_MAX_STEPS`. If it hits the cap it's forced to answer from what it gathered,
  and identical repeat tool calls are skipped.

## Configuration (`.env`)

Key settings (see `.env.example` for the full list):

- `LMSTUDIO_MODEL` — which loaded model to use (switch models here, no code change).
- `USER_NAME` — the one fact injected into every chat's system prompt.
- `AGENT_MAX_STEPS` — max tool-calling steps per turn (default 12).
- `SEARCH_PROVIDER` — `duckduckgo` (default, no key). Brave/Tavily reserved for later.
- `COMMAND_TIMEOUT_S` / `COMMAND_MAX_OUTPUT_CHARS` — `run_command` limits.
- `TOOL_MAX_FETCH_CHARS` — per-chunk cap on fetched page text.

## Notes & limitations

- **Some sites block scraping.** `fetch_url` works on many sites (BBC, docs, blogs) but
  bot-protected/JS-heavy sites (ESPN, Wikipedia, Reuters) return errors — the model falls
  back to search snippets or other sources. No headless browser (a possible future add).
- **`run_command` is powerful.** It runs arbitrary shell commands with your privileges
  (PowerShell on Windows, bash/`$SHELL` on macOS/Linux). The approval gate is the safety —
  only approve what you'd run yourself.
- Cross-platform (Windows/macOS/Linux); developed and most tested on Windows.
