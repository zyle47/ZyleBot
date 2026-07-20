"""Pure-numpy inference for the exported Breakout DQN policy.

Torch-free by design: the app must never import the training stack. A lazy
singleton serves one immutable policy object to every WebSocket. It also detects
a newer atomically-published policy (see `rl/export_policy.py`) and hot-reloads
it without a restart — the `.npz` commit marker's `st_mtime_ns` + size is the
change signal, and the stat check is throttled to at most once per second across
all sockets. A bad or missing replacement is logged and ignored, keeping the last
known-good policy in service; only a never-loaded policy raises.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Mapping

import numpy as np

from rl.features import build_observation

logger = logging.getLogger("zylebot.rl_policy")

POLICY_PATH = Path("rl/policy/breakout_policy.npz")
REQUIRED_OBSERVATION_VERSION = "level1-v1"
STAT_THROTTLE_S = 1.0
REQUIRED_ARRAYS = (
    "layer1_weight",
    "layer1_bias",
    "layer2_weight",
    "layer2_bias",
    "output_weight",
    "output_bias",
)
_EXPECTED_SHAPES = {
    "layer1_weight": (256, 78),
    "layer1_bias": (256,),
    "layer2_weight": (256, 256),
    "layer2_bias": (256,),
    "output_weight": (3, 256),
    "output_bias": (3,),
}


class PolicyUnavailableError(RuntimeError):
    """Raised when the exported policy is absent or cannot be validated."""


def _read_meta_json(path: Path) -> dict:
    """Best-effort read of the human-readable sidecar. Missing or malformed
    metadata is not fatal — the authoritative copy lives inside the NPZ."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class NumpyPolicy:
    def __init__(self, path: Path = POLICY_PATH) -> None:
        path = Path(path)
        version = None
        steps = None
        score = None
        try:
            with np.load(path, allow_pickle=False) as archive:
                self.weights = {
                    name: archive[name].astype(np.float32) for name in REQUIRED_ARRAYS
                }
                if "observation_version" in archive:
                    version = str(archive["observation_version"].item())
                if "training_steps" in archive:
                    steps = int(archive["training_steps"].item())
                if "eval_score" in archive:
                    score = float(archive["eval_score"].item())
        except (OSError, ValueError, KeyError) as exc:
            raise PolicyUnavailableError(f"Breakout policy unavailable: {exc}") from exc

        for name, shape in _EXPECTED_SHAPES.items():
            if self.weights[name].shape != shape:
                raise PolicyUnavailableError(
                    f"Breakout policy array {name} has shape {self.weights[name].shape}, "
                    f"expected {shape}"
                )

        # Fall back to meta.json only for metadata the NPZ did not carry (legacy
        # exports predate the in-NPZ scalars). For freshly published policies the
        # scalars win, so a mid-replacement stale meta.json can never mispair.
        meta = _read_meta_json(path.with_name("meta.json"))
        if version is None:
            version = meta.get("observation_version")
        if steps is None:
            steps = meta.get("training_steps")
        if score is None or (isinstance(score, float) and not np.isfinite(score)):
            meta_score = meta.get("eval_score")
            score = float(meta_score) if isinstance(meta_score, (int, float)) else None

        # A declared version that disagrees is rejected; absent version info is
        # accepted as the required version (keeps legacy/test artifacts usable).
        if version is not None and version != REQUIRED_OBSERVATION_VERSION:
            raise PolicyUnavailableError(
                f"Breakout policy observation version {version!r} != "
                f"{REQUIRED_OBSERVATION_VERSION!r}"
            )

        self.observation_version = REQUIRED_OBSERVATION_VERSION
        self.training_steps = int(steps) if steps is not None else None
        self.eval_score = (
            float(score) if isinstance(score, (int, float)) and np.isfinite(score) else None
        )

    def q_values(self, observation: np.ndarray) -> np.ndarray:
        x = np.asarray(observation, dtype=np.float32)
        if x.shape != (78,):
            raise ValueError(f"expected observation shape (78,), got {x.shape}")
        x = np.maximum(0.0, x @ self.weights["layer1_weight"].T + self.weights["layer1_bias"])
        x = np.maximum(0.0, x @ self.weights["layer2_weight"].T + self.weights["layer2_bias"])
        return x @ self.weights["output_weight"].T + self.weights["output_bias"]

    def act(self, state: Mapping[str, object]) -> int:
        return int(np.argmax(self.q_values(build_observation(state))))


# --- Lazy hot-reloading singleton -------------------------------------------
# All access happens on the asyncio event-loop thread (no thread offload), so
# these module globals need no locking: the stat/load is synchronous and cannot
# interleave with another coroutine's access.
_current: NumpyPolicy | None = None
_marker: tuple[int, int] | None = None  # (st_mtime_ns, st_size) of the committed NPZ
_last_stat_check: float = 0.0


def _stat_marker(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _maybe_reload(path: Path | None = None) -> None:
    global _current, _marker, _last_stat_check
    if path is None:
        path = POLICY_PATH  # read the module global at call time (test-overridable)
    now = time.monotonic()
    # Throttle: at most one stat check per second once a policy is in service.
    # While nothing is loaded we always retry so the first publish is picked up.
    if _current is not None and (now - _last_stat_check) < STAT_THROTTLE_S:
        return
    _last_stat_check = now

    marker = _stat_marker(path)
    if marker is None:
        # File missing/unreadable: keep the last known-good policy (if any).
        return
    if _current is not None and marker == _marker:
        return

    try:
        candidate = NumpyPolicy(path)
    except PolicyUnavailableError as exc:
        # Bad/incomplete replacement: warn, keep last good, and remember this
        # marker so we don't re-attempt the same bad file every second.
        logger.warning("Ignoring invalid Breakout policy candidate; keeping last good (%s)", exc)
        _marker = marker
        return

    _current = candidate
    _marker = marker
    logger.info(
        "Loaded Breakout policy (steps=%s, eval=%s)",
        candidate.training_steps,
        candidate.eval_score,
    )


def get_policy() -> NumpyPolicy:
    _maybe_reload()
    if _current is None:
        raise PolicyUnavailableError("Breakout policy unavailable: no valid policy has loaded")
    return _current


def act(state: Mapping[str, object]) -> int:
    return get_policy().act(state)


def _reset_for_tests() -> None:
    """Clear the singleton so a test can exercise loading from a clean slate."""
    global _current, _marker, _last_stat_check
    _current = None
    _marker = None
    _last_stat_check = 0.0
