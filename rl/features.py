"""Shared Breakout observation builder.

This module is deliberately torch-free: both the Gymnasium trainer and ZyleBot's
lightweight app-side numpy policy import it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

OBSERVATION_SIZE = 78
MAX_BALLS = 3
BRICK_COUNT = 60


def build_observation(state: Mapping[str, object]) -> np.ndarray:
    """Convert the stable browser/environment state contract to float32[78]."""
    observation = np.zeros(OBSERVATION_SIZE, dtype=np.float32)
    observation[0] = float(state["paddle_x"]) / 800.0

    balls = list(state["balls"])
    if len(balls) > MAX_BALLS:
        raise ValueError(f"expected at most {MAX_BALLS} balls, got {len(balls)}")
    # "Lowest" on screen means the largest logical y coordinate.
    for slot, ball in enumerate(sorted(balls, key=lambda item: float(item[1]), reverse=True)):
        base = 1 + slot * 5
        observation[base : base + 5] = (
            float(ball[0]) / 800.0,
            float(ball[1]) / 600.0,
            float(ball[2]) / 640.0,
            float(ball[3]) / 640.0,
            1.0,
        )

    observation[16] = float(state["speed"]) / 640.0
    observation[17] = float(state["pierce"]) / 10.0

    bricks: Sequence[Sequence[float]] = state["bricks"]  # type: ignore[assignment]
    if len(bricks) != BRICK_COUNT:
        raise ValueError(f"expected {BRICK_COUNT} bricks, got {len(bricks)}")
    observation[18:] = [
        float(hits) / float(max_hits) if float(max_hits) > 0 else 0.0
        for hits, max_hits in bricks
    ]
    return observation
