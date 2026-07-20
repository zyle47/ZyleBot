# ZyleBot

> **Local intelligence. Your machine. Your rules.**

ZyleBot is a local-first, agentic chat app that connects a lightweight FastAPI backend
to an LLM served by [LM Studio](https://lmstudio.ai/). It can reason through multi-step
tasks, inspect files and system information, search and read the web, check weather,
switch local models, and propose actions on your machine—all from a streaming web UI.

**Local inference** · **Persistent conversations** · **Human-approved actions** ·
**No frontend build step**

[Quick start](#quick-start) · [Tool arsenal](#tool-arsenal) ·
[How it works](#how-it-works) · [Configuration](#configuration) ·
[Privacy](#privacy-and-network-access) · [Limitations](#limitations)

---

## Highlights

| Capability | What it gives you |
|---|---|
| **Multi-step agent loop** | The model can reason, call a tool, inspect the result, correct course, and continue until it can answer. |
| **Visible execution** | Reasoning, tool activity, approval requests, and the final answer stream into the conversation as they happen. |
| **Local model control** | LM Studio provides inference; ZyleBot can detect the loaded model, show its context window, and switch configured models. |
| **Human authority** | File changes pause for approval. Shell commands are classified as allowed, approval-required, or blocked before execution. |
| **Persistent memory** | Conversations and messages live in a local SQLite database and survive restarts. |
| **Voice and vision** | Dictate with local `faster-whisper`, or attach an image for a dedicated vision turn. |
| **Cross-platform shell** | PowerShell is used on Windows; macOS and Linux use the configured `$SHELL` with a bash fallback. |

ZyleBot is Windows-first and primarily tested there, but its application and tool paths
support Windows, macOS, and Linux.

## Architecture at a glance

```text
Browser UI
   │  JSON requests + streamed SSE events
   ▼
FastAPI application
   ├── Agent loop ─────────────── LM Studio / local LLM
   ├── Tool registry ──────────── Filesystem, system, web, weather, shell
   ├── Conversation store ─────── SQLite
   └── Speech transcription ───── faster-whisper (local CPU by default)
```

The browser is a vanilla HTML/CSS/JavaScript client. There is no Node.js dependency or
frontend compilation step.

## Quick start

### 1. Prerequisites

- **Windows, macOS, or Linux** (Windows is the primary development platform).
- **Python 3.14**.
- **LM Studio**, with at least one tool-calling-capable chat model downloaded.
- The `lms` CLI on `PATH` if you want to use ZyleBot's in-app server-start and model-switch controls.

The configured default model is `qwythos-9b-claude-mythos-5-1m`, but ZyleBot can work
with another model that reliably emits OpenAI-compatible tool calls.

### 2. Install

**Windows / PowerShell**

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
python3.14 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

The defaults work as-is for a standard LM Studio server on `localhost:1234`. Edit `.env`
when you want a different model, user name, database location, timeout, or tool limit.

### 3. Prepare LM Studio

Start LM Studio's OpenAI-compatible local server and load a chat model. You can do this
from **Developer → Local Server** in LM Studio or with its CLI:

```powershell
lms server start
lms load <model-id>
```

Recommended starting point for a 12 GB GPU:

- **GPU offload:** maximum / all layers
- **Context length:** `65536` (64k)
- **Flash Attention:** on

Longer contexts may spill into shared GPU memory and become much slower. ZyleBot reads
the loaded context-window size and displays live usage in the header gauge.

### 4. Run ZyleBot

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). FastAPI's interactive API
reference is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

If LM Studio is unreachable after the web app opens, the header exposes a **Start
server** control that runs `lms server start`. A model still needs to be available to
answer chat requests.

## Tool arsenal

### Read-only tools

These run automatically so the agent can gather evidence without interrupting the turn.

| Tool | Purpose |
|---|---|
| `list_directory` | List contents within the ZyleBot project folder only, with basic metadata. |
| `read_file` | Read a text file up to the configured byte limit, with best-effort UTF-8 decoding. |
| `search_files` | Find files recursively with a glob pattern. |
| `get_file_info` | Inspect file or folder metadata. |
| `get_system_info` | Report OS, CPU, memory, and per-drive disk usage. |
| `web_search` | Search the web through DuckDuckGo by default. |
| `fetch_url` | Download and extract readable page text in offset-based chunks. |
| `get_weather` | Resolve a place and return current Open-Meteo conditions. |
| `get_game_scores` | Read the Breakout high-score table (play at `/game`). |

### Scoped automatic Style Lab

These two tools modify one isolated preview stylesheet without showing an approval card.
Neither accepts a destination path, and the CSS is rendered only inside the `/style-lab`
iframe. The lab is linked from the footer and refreshes changes automatically.

| Tool | Purpose |
|---|---|
| `update_style_lab_css` | Replace the complete `app/static/style-lab.css` preview stylesheet. |
| `reset_style_lab_css` | Restore the preview from the protected starter stylesheet. |

If a model selects generic `write_file` for that exact stylesheet, ZyleBot automatically
routes the call through the same scoped validator and skips the confirmation card. Every
other `write_file` destination remains approval-required.

### General file actions

These always pause the agent loop and show their exact arguments in an **Approve / Deny**
card before anything changes.

| Tool | Purpose |
|---|---|
| `write_file` | Create or overwrite a UTF-8 text file. |
| `append_file` | Append UTF-8 text, creating the file when needed. |
| `make_directory` | Create a directory and any missing parents. |

### Guarded shell commands

`run_command` uses PowerShell on Windows and the native configured shell on macOS/Linux.
Every proposed command is classified before it can run:

| Verdict | Behaviour |
|---|---|
| **Allow** | A narrow set of recognized read-only commands can run automatically. |
| **Confirm** | Anything not proven read-only pauses for explicit approval. |
| **Block** | Clearly dangerous operations—such as destructive deletion, formatting, elevation, encoded execution, and nested shells—are refused without an override. |

The command guard is a fail-closed safety layer, not a security sandbox. An approved
command runs with the same operating-system privileges as the ZyleBot process, so only
approve a command you would run yourself.

`list_directory` has an additional enforced boundary: it accepts only the ZyleBot project
folder and its descendants. Relative paths are resolved from that project folder, and
path traversal or filesystem links cannot be used to list directories outside it.

The Style Lab writer has a separate fixed boundary: it cannot choose a path, create a
file, or touch the application's own styles. It rejects oversized or incomplete CSS,
external-resource syntax, HTML style tags, null bytes, and linked targets; writes replace
the file atomically. Open `/style-lab` beside the chat to watch changes appear within
about one second.

## How it works

1. **You send a message.** The browser posts it to the local FastAPI app.
2. **ZyleBot rebuilds the context.** The selected conversation's saved messages are sent
   to the active LM Studio model; LM Studio itself remains stateless.
3. **The model answers or requests a tool.** Tool requests use validated names and
   structured arguments from ZyleBot's registry.
4. **ZyleBot enforces the boundary.** Read-only work and tightly scoped Style Lab edits
   can continue; general actions pause for approval, and blocked shell commands are refused.
5. **The result returns to the model.** It can use the evidence, call another tool,
   change direction, or finish.
6. **The browser receives a live stream.** Reasoning, tool events, approval cards, and
   answer text arrive over server-sent events.
7. **The conversation is saved.** SQLite keeps each conversation independent and restores
   it after a restart.

The loop stops after `AGENT_MAX_STEPS` tool rounds. Repeated identical calls are skipped,
and reaching the ceiling forces a final answer from the evidence already gathered.

## Model control

`models.json` maps LM Studio model IDs to friendly aliases and preferred context lengths.
The file is read fresh when model data is requested, so aliases and context settings can
be adjusted without changing application code.

At startup, ZyleBot checks what LM Studio actually has loaded and aligns its active model
to that runtime state. Switching from the header unloads the current model first so a
single-GPU machine does not accidentally hold two models in VRAM.

## Configuration

Copy `.env.example` to `.env`; the local file is intentionally ignored by Git. The most
useful settings are:

| Variable | Default | Purpose |
|---|---:|---|
| `LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` | LM Studio's OpenAI-compatible API base. |
| `LMSTUDIO_MODEL` | `qwythos-9b-claude-mythos-5-1m` | Fallback model ID before runtime detection. |
| `TEMPERATURE` | `0.3` | Sampling temperature; lower is steadier for tool selection. |
| `AGENT_MAX_STEPS` | `12` | Maximum tool-calling rounds in one turn. |
| `AGENT_REQUEST_TIMEOUT_S` | `120` | Timeout for one LM Studio request. |
| `USER_NAME` | empty | Optional fact injected into every conversation's system prompt. |
| `ZYLEBOT_DB_PATH` | `data/zylebot.db` | SQLite database location. |
| `SEARCH_PROVIDER` | `duckduckgo` | Web-search backend; DuckDuckGo needs no API key. |
| `TOOL_MAX_FETCH_CHARS` | `8000` | Maximum characters returned per fetched-page chunk. |
| `COMMAND_TIMEOUT_S` | `30` | Maximum shell-command runtime. |
| `COMMAND_MAX_OUTPUT_CHARS` | `10000` | Maximum command output returned to the model. |
| `WHISPER_MODEL_SIZE` | `small` | Local speech-recognition model size. |
| `WHISPER_DEVICE` | `cpu` | Keeps transcription from competing with the LLM for VRAM. |

See [`.env.example`](.env.example) for the complete, commented list.

## Privacy and network access

The model inference, agent loop, conversation database, file tools, and default speech
transcription run on your machine. ZyleBot does not require a hosted inference account or
an API key for its default search and weather providers.

Features that inherently need the internet still make outbound requests:

- `web_search` contacts the configured search provider.
- `fetch_url` contacts the page you ask it to read.
- `get_weather` uses Open-Meteo's geocoding and forecast APIs.
- `faster-whisper` downloads its selected model from Hugging Face on first use, then uses
  the local cache.
- LM Studio handles any model downloads you initiate there.

Conversation data stays in the configured SQLite file unless you explicitly use a tool
or command to send it elsewhere.

## Project map

```text
app/
├── main.py             FastAPI lifecycle and JSON/SSE API routes
├── pages.py            HTML page routes
├── agent_loop.py       Multi-step tool loop and approval pause/resume
├── llm_client.py       LM Studio protocol boundary
├── model_manager.py    LM Studio CLI model/server control
├── command_guard.py    Allow / confirm / block command classifier
├── db.py               SQLite persistence and migrations
├── tools/              Registered read-only and action tools
├── templates/          Jinja chat shell and content pages
└── static/             Vanilla JavaScript and CSS
models.json             Model aliases and preferred context lengths
.env.example            Documented configuration template
```

## Teaching a DQN to play Breakout

The `rl/` subproject is a from-scratch PyTorch Double-DQN for the level-one Breakout wall. Its
Gymnasium environment mirrors the browser game's fixed-step physics, while the live **AI: ON**
button uses an exported policy through a lightweight numpy-only WebSocket path in the app.
PyTorch and the training stack belong in a separate environment; they are not app dependencies:

```powershell
py -3.14 -m venv rl\venv
rl\venv\Scripts\python.exe -m pip install -r rl\requirements.txt --index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://pypi.org/simple
rl\venv\Scripts\python.exe -m rl.train
rl\venv\Scripts\python.exe -m rl.play --ckpt rl\runs\<run>\best.pt
rl\venv\Scripts\python.exe -m rl.export_policy --ckpt rl\runs\<run>\best.pt
rl\venv\Scripts\python.exe -m rl.plot --run rl\runs\<run>
```

Open `/game`, leave the game on its attract screen, then click **AI: OFF** (or press `A`) to
watch the exported `rl/policy/breakout_policy.npz` play. A random policy usually scores about
0–100; paddle tracking tends to emerge around 100k–300k training steps, and reliable level-one
clears commonly take roughly 0.5–3 million steps. Expect hours rather than days: the small
network is cheap, while CPU-side environment stepping is the main bottleneck.

Training defaults to two million steps, uses CUDA automatically when available, logs CSV data
and checkpoints under ignored `rl/runs/`, and supports `--resume <checkpoint>`. Run the RL tests
with `rl\venv\Scripts\python.exe -m unittest discover -s rl/tests -v`.

For a clean fine-tuning branch from a trained best checkpoint, use `--fork-run`. It creates a new
timestamped run, re-evaluates the source policy as that branch's baseline, and keeps the original
CSV/checkpoints untouched. `--steps` is additional work, and a resumed run preserves its saved
learning rate unless `--learning-rate` is supplied:

```powershell
rl\venv\Scripts\python.exe -m rl.train `
    --resume rl\runs\<source-run>\best.pt `
    --fork-run `
    --steps 5000000 `
    --learning-rate 0.00003 `
    --eval-episodes 10 `
    --device cuda `
    --seed 42 `
    --live-export
```

Changing the number of evaluation episodes on resume requires `--fork-run`, keeping best-score
comparisons statistically consistent. `--live-export` is opt-in and atomically publishes only a
new best policy for the browser showcase.

## AI Spectator Arena

Open [`/game/arena`](http://127.0.0.1:8000/game/arena) (also linked from `/game`) to watch several
independent copies of the exported policy play at once. The header shows the active policy's
training step and eval score plus the connection state.

- **1 / 2 / 4 / 6** picks how many games run — default `4`, hard cap `6`. `START ALL` / `STOP ALL`
  restart or halt every slot.
- **Grid** tiles are same-origin iframes that start themselves, stay muted, never submit a score,
  and loop level 1 (GAME_OVER or a level-1 clear restarts after ~1.5 s). **POP OUT** re-hosts the
  selected games as real browser windows; a popped-out slot stops its grid iframe so inference is
  not doubled, and a blocked popup degrades cleanly to a `POPUP BLOCKED` grid tile.
- **Viewer stats** (runs, level-1 clears, clear %, mean and highest final score) come from ordinary
  three-life browser runs. They are deliberately **not** the trainer's single-life `eval_score_mean`.

Arena runs read whatever policy is published in `rl/policy/`. Publishing a better one takes effect
without restarting ZyleBot:

- **Manual export** (the supported way to snapshot the currently running trainer):

  ```powershell
  $run = Get-ChildItem .\rl\runs -Directory |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1

  .\rl\venv\Scripts\python.exe -m rl.export_policy --ckpt "$($run.FullName)\best.pt"
  ```

- **`--live-export`** publishes each new best automatically during a *future* training run (above).

Either way the export is atomic (the `.npz` is replaced last, as the commit marker) and the app
hot-reloads it — a throttled stat check swaps in a valid newer policy and keeps the last known-good
one if a replacement is corrupt, wrong-shaped, or the wrong observation version. `GET
/api/game-agent/status` (polled by the arena every 2 s) reports the active policy without loading
torch or touching LM Studio.

## Limitations

- **Vision and tools are separate.** The current LM Studio stack drops image input when
  tools are attached, so image turns intentionally run without the tool registry.
- **Some sites resist extraction.** Bot-protected or JavaScript-heavy pages can reject
  `fetch_url`; the agent may fall back to search snippets or another source.
- **The shell is not sandboxed.** The guard blocks known-dangerous patterns and approval
  controls authority, but an approved command still runs as your user.
- **Large contexts cost memory.** On a 12 GB GPU, going much beyond roughly 64k tokens can
  spill into shared memory and slow inference sharply.
- **Windows gets the most testing.** macOS and Linux paths are supported but less heavily
  exercised.

## Development checks

Run the current standard-library test suite with:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s app/tests -v
```

HTML and CSS changes need only a browser refresh; static assets are served with
revalidation enabled, so there is no frontend build step.
