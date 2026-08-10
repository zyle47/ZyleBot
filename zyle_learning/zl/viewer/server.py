"""Watch a trained CNN policy play levels 1-4 live in the browser.

Server-authoritative spectator: the *real* zl environment runs here and is stepped by
the policy, then the raw game state (paddle / balls / destroyed bricks / score) is
streamed to a browser <canvas> over Server-Sent Events. What you watch is therefore
exactly what the model plays -- no separate physics in the browser to drift.

Inference runs on CPU so this never competes with a training run on the GPU. Uses only
the standard library for serving (http.server + SSE), so nothing is added to the venv.
Fully isolated: imports only zl, never app/ or rl/.

    python -m zl.viewer.server --model runs/mastery_levels/checkpoints/best_validation_model.zip
    # then open http://127.0.0.1:8100
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
from stable_baselines3 import PPO

import zl  # noqa: F401 - registers the env / shared constants
from zl.env.breakout import BreakoutEnv
from zl.env.physics import H, W


warnings.filterwarnings("ignore")

STATIC = Path(__file__).parent / "static"

_MODEL = None
_OBS_VERSION = "v2"
_MODEL_MTIME = 0.0
_MODEL_GENERATION = 0
_MODEL_LOCK = threading.Lock()


def load_model(path: str, *, reload_if_changed: bool = False) -> PPO:
    """Load and cache the policy on CPU; detect its observation version.

    With ``reload_if_changed`` the file's mtime is re-checked and a newer checkpoint is
    swapped in. Training rewrites ``best_validation_model.zip`` whenever it improves, so
    this is what makes the viewer show the agent *currently* getting better rather than
    a snapshot from whenever the server happened to start.
    """
    global _MODEL, _OBS_VERSION, _MODEL_MTIME, _MODEL_GENERATION
    with _MODEL_LOCK:
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            return _MODEL  # mid-write by the trainer; keep playing the current policy
        if _MODEL is None or (reload_if_changed and mtime != _MODEL_MTIME):
            try:
                model = PPO.load(path, device="cpu")
            except Exception:
                # A partially written checkpoint: keep the last good policy.
                if _MODEL is None:
                    raise
                return _MODEL
            channels = int(model.observation_space.shape[0])
            _OBS_VERSION = "v2" if channels == 32 else "v1"
            _MODEL = model
            _MODEL_MTIME = mtime
            _MODEL_GENERATION += 1
    return _MODEL


def _brick_payload(env: BreakoutEnv) -> list[dict]:
    """Full brick layout, sent once per episode; browser diffs destroyed ones after."""
    return [
        {
            "x": round(b.x),
            "y": round(b.y),
            "w": round(b.w),
            "h": round(b.h),
            "t": b.max_hits,
            "a": b.art or "",
        }
        for b in env.physics.bricks
    ]


class QuietHTTPServer(ThreadingHTTPServer):
    """Threading server that ignores benign client-abort disconnects.

    A browser closing an SSE stream (switching level, reload, Chrome tearing down a
    keep-alive/preconnect socket) surfaces as ConnectionError while socketserver reads
    the next request line -- one layer below the request handler, so it can't be caught
    there. These are expected and harmless; only real errors get a traceback.
    """

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console clean for training output
        pass

    def do_GET(self) -> None:
        route = urlparse(self.path)
        if route.path == "/":
            self._static("index.html", "text/html; charset=utf-8")
        elif route.path == "/viewer.js":
            self._static("viewer.js", "application/javascript; charset=utf-8")
        elif route.path == "/viewer.css":
            self._static("viewer.css", "text/css; charset=utf-8")
        elif route.path == "/stream":
            self._stream(parse_qs(route.query))
        else:
            self.send_error(404)

    def _static(self, name: str, ctype: str) -> None:
        try:
            body = (STATIC / name).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_event(self, event: str, data: dict) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _stream(self, query: dict) -> None:
        try:
            level = int(query.get("level", ["1"])[0])
        except ValueError:
            level = 1
        level = level if level in (1, 2, 3, 4) else 1
        speed = float(self.server.speed)
        model = load_model(self.server.model_path)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        seed = 20_000 + level * 101
        step_target = 1.0 / (30.0 * speed)
        try:
            while True:
                # Between attempts is the safe point to pick up a newer checkpoint, so a
                # swap never happens mid-episode.
                if self.server.watch:
                    model = load_model(self.server.model_path, reload_if_changed=True)
                env = BreakoutEnv(
                    level_mode="fixed",
                    level=level,
                    max_levels=1,
                    max_episode_steps=100_000,
                    observation_version=_OBS_VERSION,
                )
                obs, info = env.reset(seed=seed)
                self._send_event(
                    "init",
                    {
                        "level": level,
                        "boardW": round(W),
                        "boardH": round(H),
                        "paddleW": round(env.physics.paddle_w),
                        "bricks": _brick_payload(env),
                        "score": env.physics.score,
                        "lives": env.physics.lives,
                        "obs": _OBS_VERSION,
                        "generation": _MODEL_GENERATION,
                    },
                )
                prev_alive = set(range(len(env.physics.bricks)))
                terminated = truncated = False
                while not (terminated or truncated):
                    tick = time.perf_counter()
                    action, _ = model.predict(obs, deterministic=True)
                    obs, _, terminated, truncated, info = env.step(int(np.asarray(action).item()))
                    alive = {i for i, b in enumerate(env.physics.bricks) if b.alive}
                    dead = [i for i in prev_alive if i not in alive]
                    prev_alive = alive
                    self._send_event(
                        "frame",
                        {
                            "paddleX": round(env.physics.paddle_x),
                            "balls": [
                                [round(b.x), round(b.y)] for b in env.physics.balls if not b.dead
                            ],
                            "score": env.physics.score,
                            "lives": env.physics.lives,
                            "pierce": round(env.physics.pierce_remaining, 2),
                            "dead": dead,
                            "aliveCount": len(alive),
                        },
                    )
                    time.sleep(max(0.0, step_target - (time.perf_counter() - tick)))
                self._send_event(
                    "result",
                    {
                        "cleared": bool(info.get("boards_cleared", 0) >= 1),
                        "score": env.physics.score,
                        "bricksLeft": sum(1 for b in env.physics.bricks if b.alive),
                    },
                )
                seed += 1
                time.sleep(2.5)  # hold on the win/lose banner before the next attempt
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # browser switched level or closed the tab


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default="runs/mastery_levels/checkpoints/best_validation_model.zip"
    )
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="pin the checkpoint loaded at startup instead of picking up newer ones",
    )
    args = parser.parse_args()

    if not Path(args.model).exists():
        raise SystemExit(f"model not found: {args.model}")
    load_model(args.model)
    server = QuietHTTPServer(("127.0.0.1", args.port), Handler)
    server.model_path = args.model
    server.speed = max(0.1, args.speed)
    server.watch = not args.no_watch
    print(
        f"zl viewer -> http://127.0.0.1:{args.port}   "
        f"(model: {args.model}, obs {_OBS_VERSION}, CPU inference"
        f"{', live-reloading' if server.watch else ', pinned'})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
