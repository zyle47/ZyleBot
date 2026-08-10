"""Deterministic Breakout simulator, ported without runtime coupling.

The update order and collision math match the deployed 800x600 game and the proven
level-one simulator. Rendering, rewards, episode progression, and observations live
outside this module so parity can be tested on raw state alone.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from zl.env.levels import (
    LEVEL_ONE_ROW_POINTS,
    PIERCER_HITS,
    SPLITTER_HITS,
    LevelDefinition,
)

W, H = 800.0, 600.0
PHYSICS_STEP = 1.0 / 120.0
DECISION_SUBSTEPS = 4
BRICK_SIDE = 20.0
PADDLE_W, PADDLE_H, PADDLE_Y = 110.0, 14.0, 560.0
PADDLE_SPEED = 520.0
BALL_R = 7.0
BALL_BASE_SPEED = 340.0
SPEED_PER_BRICK, SPEED_PER_LEVEL, MAX_SPEED = 5.0, 40.0, 640.0
MAX_DEFLECT = math.pi / 3.0
MIN_VY_FRAC = 0.25
START_LIVES = 3
PIERCER_DURATION = 10.0
SPLIT_SPREAD = 0.55


@dataclass
class Ball:
    x: float
    y: float
    vx: float
    vy: float
    dead: bool = False


@dataclass
class Brick:
    x: float
    y: float
    w: float
    h: float
    row: int
    col: int
    alive: bool
    hits: int
    max_hits: int
    piercer: bool
    splitter: bool
    art: str | None
    points: int


@dataclass
class PhysicsEvents:
    points: int = 0
    brick_hits: int = 0
    bricks_destroyed: int = 0
    paddle_hits: int = 0
    life_lost: bool = False
    level_cleared: bool = False
    piercer_activated: bool = False
    splitter_activated: bool = False

    def merge(self, other: "PhysicsEvents") -> None:
        self.points += other.points
        self.brick_hits += other.brick_hits
        self.bricks_destroyed += other.bricks_destroyed
        self.paddle_hits += other.paddle_hits
        self.life_lost = self.life_lost or other.life_lost
        self.level_cleared = self.level_cleared or other.level_cleared
        self.piercer_activated = self.piercer_activated or other.piercer_activated
        self.splitter_activated = self.splitter_activated or other.splitter_activated


def build_bricks(level: LevelDefinition) -> list[Brick]:
    brick_w = (W - 2.0 * BRICK_SIDE - (level.cols - 1) * level.gap) / level.cols
    bricks: list[Brick] = []
    for row, line in enumerate(level.pattern):
        for col, cell in enumerate(line):
            if cell == "0":
                continue
            max_hits = int(cell) if cell.isdigit() else 1
            if level.built_in_number == 1:
                points = LEVEL_ONE_ROW_POINTS[row]
            else:
                points = 30 + (level.rows - row) * 5 + (max_hits - 1) * 20
            bricks.append(
                Brick(
                    x=BRICK_SIDE + col * (brick_w + level.gap),
                    y=level.top + row * (level.brick_h + level.gap),
                    w=brick_w,
                    h=level.brick_h,
                    row=row,
                    col=col,
                    alive=True,
                    hits=max_hits,
                    max_hits=max_hits,
                    piercer=max_hits == PIERCER_HITS,
                    splitter=max_hits == SPLITTER_HITS,
                    art=None if cell.isdigit() else cell,
                    points=points,
                )
            )
    return bricks


class BreakoutPhysics:
    """Raw state machine with browser-equivalent substeps and no Gym dependency."""

    def __init__(self) -> None:
        self.paddle_x = W / 2.0
        self.paddle_w = PADDLE_W
        self.balls: list[Ball] = []
        self.bricks: list[Brick] = []
        self.bricks_alive = 0
        self.speed = BALL_BASE_SPEED
        self.pierce_remaining = 0.0
        self.score = 0
        self.lives = START_LIVES
        self.level: LevelDefinition | None = None

    def new_game(
        self,
        level: LevelDefinition,
        rng: np.random.Generator,
        *,
        start_speed: float = BALL_BASE_SPEED,
    ) -> None:
        self.paddle_x = W / 2.0
        self.paddle_w = PADDLE_W
        self.speed = float(start_speed)
        self.pierce_remaining = 0.0
        self.score = 0
        self.lives = START_LIVES
        self.load_level(level)
        self.launch_ball(rng)

    def load_level(self, level: LevelDefinition) -> None:
        self.level = level
        self.bricks = build_bricks(level)
        self.bricks_alive = len(self.bricks)

    def launch_ball(self, rng: np.random.Generator) -> None:
        angle = (float(rng.random()) - 0.5) * (math.pi / 6.0)
        self.balls = [
            Ball(
                x=self.paddle_x,
                y=PADDLE_Y - BALL_R,
                vx=math.sin(angle) * self.speed,
                vy=-math.cos(angle) * self.speed,
            )
        ]

    @property
    def initial_board_points(self) -> int:
        return sum(brick.points * brick.max_hits for brick in self.bricks)

    def enforce_bounce(self, ball: Ball) -> None:
        magnitude = math.hypot(ball.vx, ball.vy) or self.speed
        vx_sign = -1.0 if ball.vx < 0 else 1.0
        vy_sign = -1.0 if ball.vy < 0 else 1.0
        abs_vy = abs(ball.vy) / magnitude * self.speed
        abs_vy = max(abs_vy, MIN_VY_FRAC * self.speed)
        abs_vy = min(abs_vy, self.speed)
        ball.vy = vy_sign * abs_vy
        ball.vx = vx_sign * math.sqrt(max(0.0, self.speed**2 - abs_vy**2))

    def move_paddle(self, action: int, dt: float) -> None:
        direction = 1 if action == 2 else -1 if action == 1 else 0
        self.paddle_x += direction * PADDLE_SPEED * dt
        half_width = self.paddle_w / 2.0
        self.paddle_x = max(half_width, min(self.paddle_x, W - half_width))

    def physics_step(self, action: int, dt: float = PHYSICS_STEP) -> PhysicsEvents:
        events = PhysicsEvents()
        self.move_paddle(action, dt)
        self.pierce_remaining = max(0.0, self.pierce_remaining - dt)

        spawned: list[Ball] = []
        for ball in self.balls:
            ball.x += ball.vx * dt
            ball.y += ball.vy * dt

            if ball.x - BALL_R < 0:
                ball.x = BALL_R
                ball.vx = abs(ball.vx)
                self.enforce_bounce(ball)
            elif ball.x + BALL_R > W:
                ball.x = W - BALL_R
                ball.vx = -abs(ball.vx)
                self.enforce_bounce(ball)
            if ball.y - BALL_R < 0:
                ball.y = BALL_R
                ball.vy = abs(ball.vy)
                self.enforce_bounce(ball)

            if ball.y - BALL_R > H:
                ball.dead = True
                continue

            paddle_left = self.paddle_x - self.paddle_w / 2.0
            paddle_right = self.paddle_x + self.paddle_w / 2.0
            if (
                ball.vy > 0
                and ball.x + BALL_R >= paddle_left
                and ball.x - BALL_R <= paddle_right
                and ball.y + BALL_R >= PADDLE_Y
                and ball.y - BALL_R <= PADDLE_Y + PADDLE_H
            ):
                ball.y = PADDLE_Y - BALL_R
                offset = max(-1.0, min((ball.x - self.paddle_x) / (self.paddle_w / 2.0), 1.0))
                angle = offset * MAX_DEFLECT
                ball.vx = math.sin(angle) * self.speed
                ball.vy = -math.cos(angle) * self.speed
                self.enforce_bounce(ball)
                events.paddle_hits += 1

            for brick in self.bricks:
                if not brick.alive:
                    continue
                closest_x = max(brick.x, min(ball.x, brick.x + brick.w))
                closest_y = max(brick.y, min(ball.y, brick.y + brick.h))
                dx, dy = ball.x - closest_x, ball.y - closest_y
                if dx * dx + dy * dy > BALL_R * BALL_R:
                    continue

                piercing = self.pierce_remaining > 0
                if not piercing:
                    overlap_x = min(ball.x + BALL_R, brick.x + brick.w) - max(
                        ball.x - BALL_R, brick.x
                    )
                    overlap_y = min(ball.y + BALL_R, brick.y + brick.h) - max(
                        ball.y - BALL_R, brick.y
                    )
                    if overlap_x < overlap_y:
                        if ball.x < brick.x + brick.w / 2.0:
                            ball.x = brick.x - BALL_R
                            ball.vx = -abs(ball.vx)
                        else:
                            ball.x = brick.x + brick.w + BALL_R
                            ball.vx = abs(ball.vx)
                    elif ball.y < brick.y + brick.h / 2.0:
                        ball.y = brick.y - BALL_R
                        ball.vy = -abs(ball.vy)
                    else:
                        ball.y = brick.y + brick.h + BALL_R
                        ball.vy = abs(ball.vy)

                hits_taken = brick.hits if piercing else 1
                brick.hits -= hits_taken
                gained = brick.points * hits_taken
                self.score += gained
                events.points += gained
                events.brick_hits += hits_taken
                if brick.hits == 0:
                    brick.alive = False
                    self.bricks_alive -= 1
                    events.bricks_destroyed += 1
                    self.speed = min(self.speed + SPEED_PER_BRICK, MAX_SPEED)
                if brick.piercer and not brick.alive:
                    self.pierce_remaining = PIERCER_DURATION
                    events.piercer_activated = True
                elif brick.splitter and not brick.alive:
                    for spread in (-SPLIT_SPREAD, SPLIT_SPREAD):
                        heading = math.atan2(ball.vx, -ball.vy) + spread
                        spawned.append(
                            Ball(
                                x=ball.x,
                                y=ball.y,
                                vx=math.sin(heading) * self.speed,
                                vy=-math.cos(heading) * self.speed,
                            )
                        )
                    events.splitter_activated = True
                if piercing:
                    magnitude = math.hypot(ball.vx, ball.vy) or self.speed
                    ball.vx = ball.vx / magnitude * self.speed
                    ball.vy = ball.vy / magnitude * self.speed
                else:
                    self.enforce_bounce(ball)

                if not brick.alive and self.bricks_alive == 0:
                    events.level_cleared = True
                    return events
                break

        self.balls = [ball for ball in self.balls if not ball.dead] + spawned
        if not self.balls:
            self.lives -= 1
            events.life_lost = True
        return events

    def decision_step(self, action: int) -> PhysicsEvents:
        events = PhysicsEvents()
        for _ in range(DECISION_SUBSTEPS):
            substep = self.physics_step(action)
            events.merge(substep)
            if substep.life_lost or substep.level_cleared:
                break
        return events

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe raw state contract used by golden parity fixtures."""
        return {
            "paddle_x": self.paddle_x,
            "paddle_w": self.paddle_w,
            "balls": [asdict(ball) for ball in self.balls],
            "bricks": [asdict(brick) for brick in self.bricks],
            "bricks_alive": self.bricks_alive,
            "speed": self.speed,
            "pierce_remaining": self.pierce_remaining,
            "score": self.score,
            "lives": self.lives,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Restore a golden-test state without requiring the source project."""
        self.paddle_x = float(state["paddle_x"])
        self.paddle_w = float(state.get("paddle_w", PADDLE_W))
        self.balls = [Ball(**ball) for ball in state["balls"]]
        self.bricks = [Brick(**brick) for brick in state["bricks"]]
        self.bricks_alive = int(state["bricks_alive"])
        self.speed = float(state["speed"])
        self.pierce_remaining = float(state["pierce_remaining"])
        self.score = int(state["score"])
        self.lives = int(state["lives"])

