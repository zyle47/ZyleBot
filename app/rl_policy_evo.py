"""Slot-aware hot-reloading loader for evolved Breakout policies.

The neuroevolution loop (`rl.evo.evolve --live-export`) publishes the current
generation's top-6 genomes to ``rl/policy_evo/slot{0..5}/`` plus a ``status.json``.
This module serves each slot to the live 6-brain arena, reusing the champion
loader's :class:`~app.rl_policy.NumpyPolicy` (loading, validation, numpy forward)
but with an **independent per-slot hot-reload singleton**, so each slot picks up
its newest genome mid-run. It never reads or affects the deployed champion in
``rl/policy/`` — that path is served only by `app.rl_policy`.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.rl_policy import NumpyPolicy, PolicyUnavailableError

logger = logging.getLogger("zylebot.rl_policy_evo")

EVO_DIR = Path("rl/policy_evo")
STATUS_PATH = EVO_DIR / "status.json"
SLOT_COUNT = 6
STAT_THROTTLE_S = 1.0


def slot_path(slot: int) -> Path:
    return EVO_DIR / f"slot{slot}" / "breakout_policy.npz"


class _SlotLoader:
    """One slot's hot-reload state. Mirrors `app.rl_policy`'s throttled stat/reload:
    at most one stat per second once loaded; a bad replacement keeps the last good
    genome; a never-loaded slot raises so the WS can report it as unavailable."""

    def __init__(self, slot: int) -> None:
        self.slot = slot
        self.path = slot_path(slot)
        self._current: NumpyPolicy | None = None
        self._marker: tuple[int, int] | None = None
        self._last_stat_check = 0.0

    def _stat_marker(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def get(self) -> NumpyPolicy:
        now = time.monotonic()
        if self._current is not None and (now - self._last_stat_check) < STAT_THROTTLE_S:
            return self._current
        self._last_stat_check = now

        marker = self._stat_marker()
        if marker is None:
            if self._current is not None:
                return self._current
            raise PolicyUnavailableError(f"evo slot {self.slot} has no published genome")
        if self._current is not None and marker == self._marker:
            return self._current

        try:
            candidate = NumpyPolicy(self.path)
        except PolicyUnavailableError as exc:
            self._marker = marker  # don't re-attempt this same bad file every second
            if self._current is not None:
                logger.warning(
                    "Ignoring invalid evo slot %s; keeping last good (%s)", self.slot, exc
                )
                return self._current
            raise
        self._current = candidate
        self._marker = marker
        return candidate


_loaders: dict[int, _SlotLoader] = {slot: _SlotLoader(slot) for slot in range(SLOT_COUNT)}


def get_evo_policy(slot: int) -> NumpyPolicy:
    """Return the hot-reloaded policy for ``slot`` (0..SLOT_COUNT-1)."""
    loader = _loaders.get(slot)
    if loader is None:
        raise PolicyUnavailableError(f"invalid evo slot {slot}")
    return loader.get()


def read_status() -> dict:
    """Best-effort read of the run's status.json. Absent/partial -> unavailable."""
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False}
    if not isinstance(payload, dict):
        return {"available": False}
    return payload


def _reset_for_tests() -> None:
    for loader in _loaders.values():
        loader._current = None
        loader._marker = None
        loader._last_stat_check = 0.0
