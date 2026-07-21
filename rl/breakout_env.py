"""Deterministic Gymnasium port of ZyleBot Breakout's level-one physics."""

from __future__ import annotations

import math
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from rl.features import OBSERVATION_SIZE, build_observation

W, H = 800.0, 600.0
PHYSICS_STEP = 1.0 / 120.0
DECISION_SUBSTEPS = 4
BRICK_TOP, BRICK_SIDE, BRICK_GAP, BRICK_H = 60.0, 20.0, 6.0, 22.0
PADDLE_W, PADDLE_H, PADDLE_Y = 110.0, 14.0, 560.0
PADDLE_SPEED = 520.0
BALL_R = 7.0
BALL_BASE_SPEED = 340.0
SPEED_PER_BRICK, SPEED_PER_LEVEL, MAX_SPEED = 5.0, 40.0, 640.0
MAX_DEFLECT = math.pi / 3.0
MIN_VY_FRAC = 0.25
START_LIVES = 3
PIERCER_HITS, PIERCER_DURATION = 5, 10.0
SPLITTER_HITS, SPLIT_SPREAD = 4, 0.55
LEVEL_ONE_ROW_POINTS = (70, 70, 50, 50, 30, 30)
MAX_EPISODE_STEPS = 20_000


class BreakoutEnv(gym.Env[np.ndarray, int]):
    """Single-life, level-one MDP using the browser game's exact update order."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        paddle_hit_reward: float = 0.0,
        stall_paddle_hits: int = 0,
        stall_penalty: float = 0.0,
        curriculum_clear_max: float = 0.0,
        curriculum_prob: float = 1.0,
    ) -> None:
        super().__init__()
        if not math.isfinite(paddle_hit_reward) or paddle_hit_reward < 0:
            raise ValueError("paddle_hit_reward must be a finite non-negative number")
        if stall_paddle_hits < 0:
            raise ValueError("stall_paddle_hits must be non-negative")
        if not math.isfinite(stall_penalty) or stall_penalty < 0:
            raise ValueError("stall_penalty must be a finite non-negative number")
        if bool(stall_paddle_hits) != bool(stall_penalty):
            raise ValueError("stall_paddle_hits and stall_penalty must both be enabled or disabled")
        if not math.isfinite(curriculum_clear_max) or not 0.0 <= curriculum_clear_max < 1.0:
            raise ValueError("curriculum_clear_max must be in [0, 1)")
        if not math.isfinite(curriculum_prob) or not 0.0 <= curriculum_prob <= 1.0:
            raise ValueError("curriculum_prob must be in [0, 1]")
        self.paddle_hit_reward = float(paddle_hit_reward)
        self.stall_paddle_hits = int(stall_paddle_hits)
        self.stall_penalty = float(stall_penalty)
        self.curriculum_clear_max = float(curriculum_clear_max)
        self.curriculum_prob = float(curriculum_prob)
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(
            low=-1.0,
            high=1.5,
            shape=(OBSERVATION_SIZE,),
            dtype=np.float32,
        )
        self.paddle: dict[str, float] = {"x": W / 2.0, "w": PADDLE_W}
        self.balls: list[dict[str, float | bool]] = []
        self.bricks: list[dict[str, Any]] = []
        self.bricks_alive = 0
        self.speed = BALL_BASE_SPEED
        self.pierce_remaining = 0.0
        self.score = 0
        self.episode_paddle_hits = 0
        self.paddle_hits_without_brick = 0
        self.episode_stall_penalties = 0
        self.lives = START_LIVES
        self.elapsed_steps = 0
        self._episode_done = False

    def _build_bricks(self) -> None:
        cols, rows = 10, 6
        brick_w = (W - 2.0 * BRICK_SIDE - (cols - 1) * BRICK_GAP) / cols
        self.bricks = []
        for row in range(rows):
            for col in range(cols):
                self.bricks.append(
                    {
                        "x": BRICK_SIDE + col * (brick_w + BRICK_GAP),
                        "y": BRICK_TOP + row * (BRICK_H + BRICK_GAP),
                        "w": brick_w,
                        "h": BRICK_H,
                        "row": row,
                        "col": col,
                        "alive": True,
                        "hits": 1,
                        "max_hits": 1,
                        "piercer": False,
                        "splitter": False,
                        "points": LEVEL_ONE_ROW_POINTS[row],
                    }
                )
        self.bricks_alive = len(self.bricks)

    def _launch_ball(self) -> None:
        angle = (float(self.np_random.random()) - 0.5) * (math.pi / 6.0)
        self.balls = [
            {
                "x": self.paddle["x"],
                "y": PADDLE_Y - BALL_R,
                "vx": math.sin(angle) * self.speed,
                "vy": -math.cos(angle) * self.speed,
            }
        ]

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        del options
        super().reset(seed=seed)
        self.paddle = {"x": W / 2.0, "w": PADDLE_W}
        self.speed = BALL_BASE_SPEED
        self.pierce_remaining = 0.0
        self.score = 0
        self.episode_paddle_hits = 0
        self.paddle_hits_without_brick = 0
        self.episode_stall_penalties = 0
        self.lives = START_LIVES
        self.elapsed_steps = 0
        self._episode_done = False
        self._build_bricks()
        self._apply_curriculum()
        self._launch_ball()
        return build_observation(self.state_dict()), self._info()

    def _apply_curriculum(self) -> None:
        """Randomly pre-clear part of the board so training sees mid/late-game states.

        A no-op unless ``curriculum_clear_max`` is set. Clears a uniform-random fraction in
        ``[0, curriculum_clear_max)`` of the bricks and advances the ball speed to match the
        number removed, so a partially-cleared board is a faithful fast-ball state rather than
        a slow full board with holes. Never clears the whole board (at least one brick remains).
        Deterministic under a seeded ``reset`` because it draws from ``self.np_random``.

        ``curriculum_prob`` mixes in full-board openings: with probability ``1 - curriculum_prob``
        the reset is left untouched, so the agent keeps practising the real slow-ball opening
        (which greedy eval always measures) instead of only mid-game states. ``1.0`` (default)
        applies the curriculum every episode and takes no extra draw, preserving prior behaviour.
        """
        if self.curriculum_clear_max <= 0.0:
            return
        if self.curriculum_prob < 1.0 and float(self.np_random.random()) >= self.curriculum_prob:
            return
        fraction = float(self.np_random.random()) * self.curriculum_clear_max
        clear_count = int(fraction * len(self.bricks))
        clear_count = max(0, min(clear_count, len(self.bricks) - 1))
        if clear_count == 0:
            return
        indices = self.np_random.choice(len(self.bricks), size=clear_count, replace=False)
        for index in indices:
            brick = self.bricks[int(index)]
            brick["hits"] = 0
            brick["alive"] = False
        self.bricks_alive -= clear_count
        self.speed = min(BALL_BASE_SPEED + clear_count * SPEED_PER_BRICK, MAX_SPEED)

    def state_dict(self) -> dict[str, object]:
        """Return the plain-data state contract shared with the browser bridge."""
        return {
            "paddle_x": self.paddle["x"],
            "balls": [
                [ball["x"], ball["y"], ball["vx"], ball["vy"]]
                for ball in self.balls
                if not ball.get("dead", False)
            ],
            "speed": self.speed,
            "pierce": self.pierce_remaining,
            "bricks": [
                (brick["hits"] if brick["alive"] else 0, brick["max_hits"])
                for brick in self.bricks
            ],
        }

    def get_raw_state(self) -> dict[str, Any]:
        """Expose live simulation containers for parity tests and diagnostics."""
        return {
            "paddle": self.paddle,
            "balls": self.balls,
            "bricks": self.bricks,
            "speed": self.speed,
            "pierce": self.pierce_remaining,
        }

    def set_raw_state(self, state: dict[str, Any]) -> None:
        """Apply a state returned by :meth:`get_raw_state` and refresh counts."""
        self.paddle = state["paddle"]
        self.balls = state["balls"]
        self.bricks = state["bricks"]
        self.speed = float(state["speed"])
        self.pierce_remaining = float(state["pierce"])
        self.bricks_alive = sum(bool(brick["alive"]) for brick in self.bricks)

    def _info(
        self,
        *,
        life_lost: bool = False,
        level_clear: bool = False,
        paddle_hits: int = 0,
        stall_penalties: int = 0,
    ) -> dict[str, Any]:
        return {
            "score": self.score,
            "lives": self.lives,
            "bricks_alive": self.bricks_alive,
            "life_lost": life_lost,
            "level_clear": level_clear,
            "paddle_hits": paddle_hits,
            "episode_paddle_hits": self.episode_paddle_hits,
            "paddle_hits_without_brick": self.paddle_hits_without_brick,
            "stall_penalties": stall_penalties,
            "episode_stall_penalties": self.episode_stall_penalties,
            "state": self.state_dict(),
        }

    def enforce_bounce(self, ball: dict[str, float | bool]) -> None:
        """Mirror game.js enforceBounce, including sign behavior for zero axes."""
        vx, vy = float(ball["vx"]), float(ball["vy"])
        magnitude = math.hypot(vx, vy) or self.speed
        vx_sign = -1.0 if vx < 0 else 1.0
        vy_sign = -1.0 if vy < 0 else 1.0
        abs_vy = abs(vy) / magnitude * self.speed
        abs_vy = max(abs_vy, MIN_VY_FRAC * self.speed)
        abs_vy = min(abs_vy, self.speed)
        ball["vy"] = vy_sign * abs_vy
        ball["vx"] = vx_sign * math.sqrt(max(0.0, self.speed**2 - abs_vy**2))

    def _move_paddle(self, action: int, dt: float) -> None:
        direction = 1 if action == 2 else -1 if action == 1 else 0
        self.paddle["x"] += direction * PADDLE_SPEED * dt
        half_width = self.paddle["w"] / 2.0
        self.paddle["x"] = max(half_width, min(self.paddle["x"], W - half_width))

    def _physics_step(self, action: int, dt: float) -> tuple[int, int, bool, bool]:
        scored = 0
        paddle_hits = 0
        self._move_paddle(action, dt)
        self.pierce_remaining = max(0.0, self.pierce_remaining - dt)

        spawned: list[dict[str, float | bool]] = []
        for ball in self.balls:
            ball["x"] = float(ball["x"]) + float(ball["vx"]) * dt
            ball["y"] = float(ball["y"]) + float(ball["vy"]) * dt

            if float(ball["x"]) - BALL_R < 0:
                ball["x"] = BALL_R
                ball["vx"] = abs(float(ball["vx"]))
                self.enforce_bounce(ball)
            elif float(ball["x"]) + BALL_R > W:
                ball["x"] = W - BALL_R
                ball["vx"] = -abs(float(ball["vx"]))
                self.enforce_bounce(ball)
            if float(ball["y"]) - BALL_R < 0:
                ball["y"] = BALL_R
                ball["vy"] = abs(float(ball["vy"]))
                self.enforce_bounce(ball)

            if float(ball["y"]) - BALL_R > H:
                ball["dead"] = True
                continue

            paddle_left = self.paddle["x"] - self.paddle["w"] / 2.0
            paddle_right = self.paddle["x"] + self.paddle["w"] / 2.0
            if (
                float(ball["vy"]) > 0
                and float(ball["x"]) + BALL_R >= paddle_left
                and float(ball["x"]) - BALL_R <= paddle_right
                and float(ball["y"]) + BALL_R >= PADDLE_Y
                and float(ball["y"]) - BALL_R <= PADDLE_Y + PADDLE_H
            ):
                ball["y"] = PADDLE_Y - BALL_R
                offset = max(
                    -1.0,
                    min((float(ball["x"]) - self.paddle["x"]) / (self.paddle["w"] / 2.0), 1.0),
                )
                angle = offset * MAX_DEFLECT
                ball["vx"] = math.sin(angle) * self.speed
                ball["vy"] = -math.cos(angle) * self.speed
                self.enforce_bounce(ball)
                paddle_hits += 1

            for brick in self.bricks:
                if not brick["alive"]:
                    continue
                closest_x = max(brick["x"], min(float(ball["x"]), brick["x"] + brick["w"]))
                closest_y = max(brick["y"], min(float(ball["y"]), brick["y"] + brick["h"]))
                dx = float(ball["x"]) - closest_x
                dy = float(ball["y"]) - closest_y
                if dx * dx + dy * dy > BALL_R * BALL_R:
                    continue

                piercing = self.pierce_remaining > 0
                if not piercing:
                    overlap_x = min(float(ball["x"]) + BALL_R, brick["x"] + brick["w"]) - max(
                        float(ball["x"]) - BALL_R, brick["x"]
                    )
                    overlap_y = min(float(ball["y"]) + BALL_R, brick["y"] + brick["h"]) - max(
                        float(ball["y"]) - BALL_R, brick["y"]
                    )
                    if overlap_x < overlap_y:
                        if float(ball["x"]) < brick["x"] + brick["w"] / 2.0:
                            ball["x"] = brick["x"] - BALL_R
                            ball["vx"] = -abs(float(ball["vx"]))
                        else:
                            ball["x"] = brick["x"] + brick["w"] + BALL_R
                            ball["vx"] = abs(float(ball["vx"]))
                    elif float(ball["y"]) < brick["y"] + brick["h"] / 2.0:
                        ball["y"] = brick["y"] - BALL_R
                        ball["vy"] = -abs(float(ball["vy"]))
                    else:
                        ball["y"] = brick["y"] + brick["h"] + BALL_R
                        ball["vy"] = abs(float(ball["vy"]))

                hits_taken = brick["hits"] if piercing else 1
                brick["hits"] -= hits_taken
                points = brick["points"] * hits_taken
                self.score += points
                scored += points
                if brick["hits"] == 0:
                    brick["alive"] = False
                    self.bricks_alive -= 1
                    self.speed = min(self.speed + SPEED_PER_BRICK, MAX_SPEED)
                if brick["piercer"] and not brick["alive"]:
                    self.pierce_remaining = PIERCER_DURATION
                elif brick["splitter"] and not brick["alive"]:
                    for spread in (-SPLIT_SPREAD, SPLIT_SPREAD):
                        heading = math.atan2(float(ball["vx"]), -float(ball["vy"])) + spread
                        spawned.append(
                            {
                                "x": float(ball["x"]),
                                "y": float(ball["y"]),
                                "vx": math.sin(heading) * self.speed,
                                "vy": -math.cos(heading) * self.speed,
                            }
                        )
                if piercing:
                    magnitude = math.hypot(float(ball["vx"]), float(ball["vy"])) or self.speed
                    ball["vx"] = float(ball["vx"]) / magnitude * self.speed
                    ball["vy"] = float(ball["vy"]) / magnitude * self.speed
                else:
                    self.enforce_bounce(ball)

                if not brick["alive"] and self.bricks_alive == 0:
                    return scored, paddle_hits, False, True
                break

        self.balls = [ball for ball in self.balls if not ball.get("dead", False)] + spawned
        if not self.balls:
            self.lives -= 1
            return scored, paddle_hits, True, False
        return scored, paddle_hits, False, False

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        if self._episode_done:
            raise RuntimeError("step() called after episode ended; call reset()")
        if not self.action_space.contains(action):
            raise ValueError(f"invalid action: {action}")

        reward = 0.0
        paddle_hits = 0
        stall_penalties = 0
        life_lost = False
        level_clear = False
        for _ in range(DECISION_SUBSTEPS):
            scored, substep_paddle_hits, life_lost, level_clear = self._physics_step(
                int(action), PHYSICS_STEP
            )
            paddle_hits += substep_paddle_hits
            reward += scored / 100.0 + substep_paddle_hits * self.paddle_hit_reward
            if scored:
                self.paddle_hits_without_brick = 0
            elif substep_paddle_hits and self.stall_paddle_hits:
                self.paddle_hits_without_brick += substep_paddle_hits
                while self.paddle_hits_without_brick >= self.stall_paddle_hits:
                    reward -= self.stall_penalty
                    stall_penalties += 1
                    self.paddle_hits_without_brick -= self.stall_paddle_hits
            if life_lost or level_clear:
                break

        if life_lost:
            reward -= 5.0
        if level_clear:
            reward += 5.0
        self.episode_paddle_hits += paddle_hits
        self.episode_stall_penalties += stall_penalties
        self.elapsed_steps += 1
        terminated = life_lost or level_clear
        truncated = not terminated and self.elapsed_steps >= MAX_EPISODE_STEPS
        self._episode_done = terminated or truncated
        observation = build_observation(self.state_dict())
        return observation, reward, terminated, truncated, self._info(
            life_lost=life_lost,
            level_clear=level_clear,
            paddle_hits=paddle_hits,
            stall_penalties=stall_penalties,
        )
