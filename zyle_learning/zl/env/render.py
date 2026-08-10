"""Rasterize raw game state into fixed-size semantic image channels."""

from __future__ import annotations

import math

import numpy as np

from zl.config import BASE_CHANNELS, LEGACY_BASE_CHANNELS, OBSERVATION_SIZE
from zl.env.physics import BALL_R, H, PADDLE_H, PADDLE_Y, PIERCER_DURATION, W, BreakoutPhysics

BRICK_OCCUPANCY = 0
BRICK_REMAINING_HITS = 1
BRICK_MAX_HITS = 2
PIERCER_CELLS = 3
SPLITTER_CELLS = 4
PADDLE = 5
BALLS = 6
PIERCE_ACTIVE = 7


def _bounds(x: float, y: float, w: float, h: float, size: int) -> tuple[int, int, int, int]:
    x0 = max(0, min(size - 1, int(math.floor(x / W * size))))
    y0 = max(0, min(size - 1, int(math.floor(y / H * size))))
    x1 = max(x0 + 1, min(size, int(math.ceil((x + w) / W * size))))
    y1 = max(y0 + 1, min(size, int(math.ceil((y + h) / H * size))))
    return x0, y0, x1, y1


def _fill_rect(channel: np.ndarray, x: float, y: float, w: float, h: float, value: int) -> None:
    size = channel.shape[0]
    x0, y0, x1, y1 = _bounds(x, y, w, h, size)
    channel[y0:y1, x0:x1] = value


def _fill_ball(channel: np.ndarray, x: float, y: float, value: int) -> None:
    size = channel.shape[0]
    cx = int(round(x / W * (size - 1)))
    cy = int(round(y / H * (size - 1)))
    # A minimum two-pixel radius keeps the ball visible to the first CNN stride.
    radius = max(2, int(math.ceil(BALL_R / min(W, H) * size)))
    yy, xx = np.ogrid[:size, :size]
    channel[(xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2] = value


def render_frame(
    physics: BreakoutPhysics,
    *,
    size: int = OBSERVATION_SIZE,
    observation_version: str = "v2",
) -> np.ndarray:
    """Return semantic uint8 CxHxW channels, independent of grid dimensions.

    Version 2 makes intact durability observable by separating remaining hits from
    maximum hits. Version 1 preserves the original seven-channel checkpoint contract.
    """
    if observation_version not in ("v1", "v2"):
        raise ValueError(f"unknown observation version: {observation_version}")
    legacy = observation_version == "v1"
    channel_count = LEGACY_BASE_CHANNELS if legacy else BASE_CHANNELS
    frame = np.zeros((channel_count, size, size), dtype=np.uint8)
    if legacy:
        piercer_channel, splitter_channel = 2, 3
        paddle_channel, ball_channel, active_channel = 4, 5, 6
    else:
        piercer_channel, splitter_channel = PIERCER_CELLS, SPLITTER_CELLS
        paddle_channel, ball_channel, active_channel = PADDLE, BALLS, PIERCE_ACTIVE
    for brick in physics.bricks:
        if not brick.alive:
            continue
        _fill_rect(frame[BRICK_OCCUPANCY], brick.x, brick.y, brick.w, brick.h, 255)
        if legacy:
            durability = int(round(255 * brick.hits / brick.max_hits))
            _fill_rect(frame[1], brick.x, brick.y, brick.w, brick.h, durability)
        else:
            remaining = int(round(255 * brick.hits / 5))
            maximum = int(round(255 * brick.max_hits / 5))
            _fill_rect(
                frame[BRICK_REMAINING_HITS], brick.x, brick.y, brick.w, brick.h, remaining
            )
            _fill_rect(frame[BRICK_MAX_HITS], brick.x, brick.y, brick.w, brick.h, maximum)
        if brick.piercer:
            _fill_rect(frame[piercer_channel], brick.x, brick.y, brick.w, brick.h, 255)
        if brick.splitter:
            _fill_rect(frame[splitter_channel], brick.x, brick.y, brick.w, brick.h, 255)

    _fill_rect(
        frame[paddle_channel],
        physics.paddle_x - physics.paddle_w / 2.0,
        PADDLE_Y,
        physics.paddle_w,
        PADDLE_H,
        255,
    )
    for ball in physics.balls:
        if not ball.dead:
            _fill_ball(frame[ball_channel], ball.x, ball.y, 255)
    if physics.pierce_remaining > 0:
        value = int(round(255 * min(1.0, physics.pierce_remaining / PIERCER_DURATION)))
        frame[active_channel, :, :] = value
    return frame
