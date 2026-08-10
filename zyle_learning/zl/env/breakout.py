"""Gymnasium environment for multi-layout, multi-life Breakout training."""

from __future__ import annotations

from collections import deque
from typing import Any, Literal

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from zl.config import (
    BASE_CHANNELS,
    FRAME_STACK,
    HELD_OUT_LEVEL,
    LEGACY_BASE_CHANNELS,
    OBSERVATION_CHANNELS,
    OBSERVATION_SIZE,
    TRAIN_BUILTIN_LEVELS,
)
from zl.env.levels import LevelDefinition, builtin_level, procedural_level
from zl.env.physics import (
    BALL_BASE_SPEED,
    MAX_SPEED,
    SPEED_PER_BRICK,
    SPEED_PER_LEVEL,
    BreakoutPhysics,
)
from zl.env.render import render_frame

LevelMode = Literal["fixed", "mixed", "procedural"]


class BreakoutEnv(gym.Env[np.ndarray, int]):
    """A seedable image-observation environment with explicit held-out isolation.

    Actions are 0=stay, 1=left, 2=right. By default one level is one episode;
    training can set ``max_levels`` higher to learn browser-style progression while
    every next board is still sampled only from the permitted training distribution.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        level_mode: LevelMode = "fixed",
        level: int = 1,
        held_out_level: int = HELD_OUT_LEVEL,
        training_levels: tuple[int, ...] = TRAIN_BUILTIN_LEVELS,
        training_level_weights: tuple[float, ...] | None = None,
        procedural_probability: float = 0.75,
        procedural_large_probability: float | None = None,
        max_levels: int = 1,
        max_episode_steps: int = 100_000,
        observation_size: int = OBSERVATION_SIZE,
        frame_stack: int = FRAME_STACK,
        clear_bonus_scale: float = 2.0,
        progression_bonus: float = 1.0,
        life_loss_penalty: float = 0.15,
        curriculum_clear_min: float = 0.0,
        curriculum_clear_max: float = 0.0,
        curriculum_probability: float = 1.0,
        curriculum_bricks_min: int = 0,
        curriculum_bricks_max: int = 0,
        action_change_penalty: float = 0.0,
        observation_version: str = "v2",
    ) -> None:
        super().__init__()
        if level_mode not in ("fixed", "mixed", "procedural"):
            raise ValueError(f"invalid level_mode: {level_mode}")
        if not 0.0 <= procedural_probability <= 1.0:
            raise ValueError("procedural_probability must be in [0, 1]")
        if procedural_large_probability is not None and not 0.0 <= procedural_large_probability <= 1.0:
            raise ValueError("procedural_large_probability must be in [0, 1]")
        if max_levels < 1 or max_episode_steps < 1 or frame_stack < 1:
            raise ValueError("max_levels, max_episode_steps, and frame_stack must be positive")
        if not 0.0 <= curriculum_clear_min <= curriculum_clear_max < 1.0:
            raise ValueError("curriculum clear bounds must satisfy 0 <= min <= max < 1")
        if not 0.0 <= curriculum_probability <= 1.0:
            raise ValueError("curriculum_probability must be in [0, 1]")
        if curriculum_bricks_min < 0 or curriculum_bricks_max < 0:
            raise ValueError("curriculum brick counts must be non-negative")
        if action_change_penalty < 0.0:
            raise ValueError("action_change_penalty must be non-negative")
        if curriculum_bricks_max and curriculum_bricks_min > curriculum_bricks_max:
            raise ValueError("curriculum_bricks_min must not exceed curriculum_bricks_max")
        if observation_version not in ("v1", "v2"):
            raise ValueError("observation_version must be 'v1' or 'v2'")
        if level_mode == "mixed" and held_out_level in training_levels:
            raise ValueError("held-out level must not appear in training_levels")

        self.level_mode = level_mode
        self.fixed_level = int(level)
        self.held_out_level = int(held_out_level)
        self.training_levels = tuple(int(item) for item in training_levels)
        if training_level_weights is None:
            self._level_weights: np.ndarray | None = None
        else:
            weights = np.asarray(training_level_weights, dtype=np.float64)
            if len(weights) != len(self.training_levels):
                raise ValueError("training_level_weights must match training_levels length")
            if np.any(weights < 0) or weights.sum() <= 0:
                raise ValueError("training_level_weights must be non-negative with a positive sum")
            self._level_weights = weights / weights.sum()
        self.procedural_probability = float(procedural_probability)
        self.procedural_large_probability = (
            float(procedural_large_probability)
            if procedural_large_probability is not None
            else None
        )
        self.max_levels = int(max_levels)
        self.max_episode_steps = int(max_episode_steps)
        self.observation_size = int(observation_size)
        self.frame_stack = int(frame_stack)
        self.clear_bonus_scale = float(clear_bonus_scale)
        self.progression_bonus = float(progression_bonus)
        self.life_loss_penalty = float(life_loss_penalty)
        self.curriculum_clear_min = float(curriculum_clear_min)
        self.curriculum_clear_max = float(curriculum_clear_max)
        self.curriculum_probability = float(curriculum_probability)
        self.curriculum_bricks_min = int(curriculum_bricks_min)
        self.curriculum_bricks_max = int(curriculum_bricks_max)
        self.action_change_penalty = float(action_change_penalty)
        self.observation_version = observation_version

        self.action_space = spaces.Discrete(3)
        base_channels = BASE_CHANNELS if observation_version == "v2" else LEGACY_BASE_CHANNELS
        channels = base_channels * frame_stack
        self.observation_space = spaces.Box(
            low=0,
            high=255,
            shape=(channels, observation_size, observation_size),
            dtype=np.uint8,
        )
        self.physics = BreakoutPhysics()
        self.frames: deque[np.ndarray] = deque(maxlen=frame_stack)
        self.elapsed_steps = 0
        self.boards_cleared = 0
        self.boards_attempted = 0
        self.total_initial_bricks = 0
        self.total_destroyed_bricks = 0
        self.current_initial_bricks = 0
        self.current_initial_points = 0
        self.curriculum_cleared_bricks = 0
        self.current_level: LevelDefinition | None = None
        self._episode_done = False
        self._previous_action: int | None = None
        self.action_changes = 0

    def _sample_level(self) -> LevelDefinition:
        if self.level_mode == "fixed":
            return builtin_level(self.fixed_level, self.np_random)
        if self.level_mode == "procedural":
            return procedural_level(
                self.np_random, large_probability=self.procedural_large_probability
            )
        if float(self.np_random.random()) < self.procedural_probability:
            return procedural_level(
                self.np_random, large_probability=self.procedural_large_probability
            )
        if self._level_weights is not None:
            chosen = int(self.np_random.choice(np.asarray(self.training_levels), p=self._level_weights))
        else:
            chosen = self.training_levels[int(self.np_random.integers(len(self.training_levels)))]
        if chosen == self.held_out_level:
            raise RuntimeError("held-out level leaked into training distribution")
        return builtin_level(chosen, self.np_random)

    def _load_board(self, *, first: bool) -> None:
        self.current_level = self._sample_level()
        built_in_index = self.current_level.built_in_number or 1
        # A directly selected built-in starts at the same speed as the browser's
        # startGame(level). Random progression never becomes easier than either
        # the selected built-in or the number of boards already cleared.
        speed_level = max(built_in_index, self.boards_cleared + 1)
        speed = min(BALL_BASE_SPEED + (speed_level - 1) * SPEED_PER_LEVEL, MAX_SPEED)
        if first:
            self.physics.new_game(self.current_level, self.np_random, start_speed=speed)
        else:
            self.physics.speed = speed
            self.physics.load_level(self.current_level)
            self.physics.launch_ball(self.np_random)
        # Record the full board's value before curriculum removal. A completion is
        # therefore worth a full-board bonus even when an episode starts in the
        # endgame; otherwise the very states meant to teach finishing would carry
        # the weakest terminal signal.
        self.current_initial_points = self.physics.initial_board_points
        self.curriculum_cleared_bricks = self._apply_curriculum()
        self.boards_attempted += 1
        self.current_initial_bricks = self.physics.bricks_alive
        self.total_initial_bricks += self.current_initial_bricks

    def _apply_curriculum(self) -> int:
        """Seedably start some training episodes at faithful mid/endgame states.

        Two modes. The *fraction* mode clears a proportion of the board, which does not
        transfer across board sizes: 0.9 leaves 6 bricks on level 1 but 115 on level 4,
        so a big board never actually practices an endgame. The *bricks-remaining* mode
        (``curriculum_bricks_max > 0``, which takes precedence) instead starts every
        board with a comparable number of bricks left, so level 4 gets the same endgame
        drilling as level 1 -- the states it otherwise never survives long enough to see.
        """
        total = len(self.physics.bricks)
        by_count = self.curriculum_bricks_max > 0
        if not by_count and self.curriculum_clear_max <= 0.0:
            return 0
        if (
            self.curriculum_probability < 1.0
            and float(self.np_random.random()) >= self.curriculum_probability
        ):
            return 0
        if by_count:
            # Clamp the band to THIS board before drawing. Clamping the drawn value
            # instead would collapse every draw above the board size into "full board",
            # so a wide band tuned for level 4 (e.g. 8-600) would starve a 59-brick
            # level of endgame practice -- which cost level 3 its clear rate once.
            low = max(1, self.curriculum_bricks_min)
            high = min(max(low, self.curriculum_bricks_max), total)
            low = min(low, high)
            remaining = int(self.np_random.integers(low, high + 1))
            clear_count = total - remaining
        else:
            span = self.curriculum_clear_max - self.curriculum_clear_min
            fraction = self.curriculum_clear_min + float(self.np_random.random()) * span
            clear_count = int(fraction * total)
        clear_count = min(clear_count, total - 1)
        if clear_count <= 0:
            return 0
        indices = self.np_random.choice(len(self.physics.bricks), size=clear_count, replace=False)
        for index in indices:
            brick = self.physics.bricks[int(index)]
            brick.hits = 0
            brick.alive = False
        self.physics.bricks_alive -= clear_count
        self.physics.speed = min(
            self.physics.speed + clear_count * SPEED_PER_BRICK,
            MAX_SPEED,
        )
        # new_game/load_level launched at the pre-curriculum speed. Relaunch so
        # velocity magnitude agrees with the advanced midgame speed.
        self.physics.launch_ball(self.np_random)
        return clear_count

    def _reset_frames(self) -> np.ndarray:
        frame = render_frame(
            self.physics,
            size=self.observation_size,
            observation_version=self.observation_version,
        )
        self.frames.clear()
        for _ in range(self.frame_stack):
            self.frames.append(frame.copy())
        return self._observation()

    def _observation(self) -> np.ndarray:
        return np.concatenate(tuple(self.frames), axis=0)

    def _append_frame(self) -> np.ndarray:
        self.frames.append(
            render_frame(
                self.physics,
                size=self.observation_size,
                observation_version=self.observation_version,
            )
        )
        return self._observation()

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        if options and "level" in options:
            self.fixed_level = int(options["level"])
            self.level_mode = "fixed"
        self.elapsed_steps = 0
        self.boards_cleared = 0
        self.boards_attempted = 0
        self.total_initial_bricks = 0
        self.total_destroyed_bricks = 0
        self._episode_done = False
        self._previous_action = None
        self.action_changes = 0
        self._load_board(first=True)
        return self._reset_frames(), self._info()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        if self._episode_done:
            raise RuntimeError("step() called after episode ended; call reset()")
        if not self.action_space.contains(action):
            raise ValueError(f"invalid action: {action}")

        # Discrete left/stay/right control makes the paddle jump ~17 px per decision, so
        # the policy holds position by oscillating (~50% of decisions flip direction).
        # That leaves the paddle moving ~14 px at ball contact, and since the bounce
        # angle is offset/half_width * 60 degrees, it costs roughly +/-15 degrees of aim
        # -- exactly what is needed to finish the last few multi-hit bricks. This makes
        # restlessness cost something, without changing the game itself.
        if self._previous_action is not None and int(action) != self._previous_action:
            self.action_changes += 1
            change_cost = self.action_change_penalty
        else:
            change_cost = 0.0
        self._previous_action = int(action)

        events = self.physics.decision_step(int(action))
        # Reward is a fraction of the board's own value, not raw points. Board value
        # spans ~80x across the training distribution (level 1 is worth 3,000 points,
        # level 4 is worth 106,560), so raw points made the same achievement worth
        # wildly different amounts per level and the value function could not fit it.
        # Normalizing means clearing any board is worth the same everywhere.
        reward = events.points / max(1, self.current_initial_points) - change_cost
        self.total_destroyed_bricks += events.bricks_destroyed
        terminated = False
        board_cleared = events.level_cleared

        if events.life_lost:
            reward -= self.life_loss_penalty
            if self.physics.lives <= 0:
                terminated = True
            else:
                self.physics.launch_ball(self.np_random)

        if board_cleared:
            self.boards_cleared += 1
            # Flat and level-independent. Farming a whole board is worth 1.0, so a
            # clear paying 3.0 makes finishing unambiguously the best strategy on
            # every layout instead of only on the point-rich ones.
            reward += self.clear_bonus_scale
            reward += self.progression_bonus
            if self.boards_cleared >= self.max_levels:
                terminated = True
            else:
                self._load_board(first=False)

        self.elapsed_steps += 1
        truncated = not terminated and self.elapsed_steps >= self.max_episode_steps
        self._episode_done = terminated or truncated
        if board_cleared and not terminated:
            observation = self._reset_frames()
        else:
            observation = self._append_frame()
        return observation, reward, terminated, truncated, self._info(
            life_lost=events.life_lost,
            board_cleared=board_cleared,
            points_gained=events.points,
            brick_hits=events.brick_hits,
            paddle_hits=events.paddle_hits,
        )

    def _info(
        self,
        *,
        life_lost: bool = False,
        board_cleared: bool = False,
        points_gained: int = 0,
        brick_hits: int = 0,
        paddle_hits: int = 0,
    ) -> dict[str, Any]:
        fraction = (
            self.total_destroyed_bricks / self.total_initial_bricks
            if self.total_initial_bricks
            else 0.0
        )
        return {
            "score": self.physics.score,
            "lives": self.physics.lives,
            "level_name": self.current_level.name if self.current_level else None,
            "level_family": self.current_level.family if self.current_level else None,
            "built_in_level": self.current_level.built_in_number if self.current_level else None,
            "bricks_alive": self.physics.bricks_alive,
            "bricks_cleared_fraction": fraction,
            "boards_cleared": self.boards_cleared,
            "boards_attempted": self.boards_attempted,
            "life_lost": life_lost,
            "board_cleared": board_cleared,
            "points_gained": points_gained,
            "brick_hits": brick_hits,
            "paddle_hits": paddle_hits,
            "pierce_remaining": self.physics.pierce_remaining,
            "ball_count": len(self.physics.balls),
            "curriculum_cleared_bricks": self.curriculum_cleared_bricks,
            "observation_version": self.observation_version,
            "action_changes": self.action_changes,
        }
