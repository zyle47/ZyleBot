from __future__ import annotations

import numpy as np

from zl.config import LEGACY_OBSERVATION_CHANNELS, OBSERVATION_CHANNELS, OBSERVATION_SIZE
from zl.env.breakout import BreakoutEnv
from zl.env.levels import builtin_level
from zl.env.physics import BreakoutPhysics
from zl.env.physics import BALL_BASE_SPEED, SPEED_PER_LEVEL
from zl.env.render import render_frame


def _render(level_number: int) -> np.ndarray:
    rng = np.random.default_rng(77)
    physics = BreakoutPhysics()
    physics.new_game(builtin_level(level_number, rng), rng)
    return render_frame(physics)


def test_render_is_fixed_size_for_small_and_tribute_levels() -> None:
    level1 = _render(1)
    level4 = _render(4)
    assert level1.shape == level4.shape == (8, OBSERVATION_SIZE, OBSERVATION_SIZE)
    assert level1.dtype == level4.dtype == np.uint8
    assert np.count_nonzero(level1[0]) > 0
    assert np.count_nonzero(level4[0]) > np.count_nonzero(level1[0])


def test_gym_observation_stacks_four_semantic_frames() -> None:
    env = BreakoutEnv(level_mode="fixed", level=1)
    observation, info = env.reset(seed=10)
    assert observation.shape == (OBSERVATION_CHANNELS, OBSERVATION_SIZE, OBSERVATION_SIZE)
    assert env.observation_space.contains(observation)
    assert info["built_in_level"] == 1
    next_observation, _, terminated, truncated, _ = env.step(0)
    assert env.observation_space.contains(next_observation)
    assert not terminated
    assert not truncated


def test_intact_brick_max_durability_is_visible_and_legacy_shape_is_supported() -> None:
    rng = np.random.default_rng(12)
    physics = BreakoutPhysics()
    physics.new_game(builtin_level(3, rng), rng)
    frame = render_frame(physics)
    maxima = set(np.unique(frame[2]))
    assert {0, 102, 153, 204, 255}.issubset(maxima)
    legacy = BreakoutEnv(level_mode="fixed", level=1, observation_version="v1")
    observation, info = legacy.reset(seed=12)
    assert observation.shape[0] == LEGACY_OBSERVATION_CHANNELS
    assert info["observation_version"] == "v1"


def test_level_four_is_rejected_if_it_leaks_into_mixed_training() -> None:
    try:
        BreakoutEnv(level_mode="mixed", held_out_level=4, training_levels=(1, 2, 3, 4))
    except ValueError as error:
        assert "held-out" in str(error)
    else:
        raise AssertionError("held-out leakage was not rejected")


def test_partial_board_curriculum_is_seeded_and_keeps_a_live_brick() -> None:
    kwargs = dict(
        level_mode="fixed",
        level=1,
        curriculum_clear_min=0.8,
        curriculum_clear_max=0.8,
    )
    first = BreakoutEnv(**kwargs)
    second = BreakoutEnv(**kwargs)
    first.reset(seed=808)
    second.reset(seed=808)
    assert first.physics.snapshot() == second.physics.snapshot()
    assert first.curriculum_cleared_bricks == 48
    assert first.physics.bricks_alive == 12
    assert first.physics.bricks_alive >= 1
    assert np.hypot(first.physics.balls[0].vx, first.physics.balls[0].vy) == first.physics.speed


def test_curriculum_can_mix_in_full_board_openings() -> None:
    env = BreakoutEnv(
        level_mode="fixed",
        level=1,
        curriculum_clear_max=0.9,
        curriculum_probability=0.0,
    )
    _, info = env.reset(seed=909)
    assert info["curriculum_cleared_bricks"] == 0
    assert info["bricks_alive"] == 60


def test_direct_builtin_start_uses_browser_level_speed() -> None:
    env = BreakoutEnv(level_mode="fixed", level=4)
    env.reset(seed=404)
    assert env.physics.speed == BALL_BASE_SPEED + 3 * SPEED_PER_LEVEL
    assert np.hypot(env.physics.balls[0].vx, env.physics.balls[0].vy) == env.physics.speed


def test_both_render_versions_handle_a_cleared_board() -> None:
    rng = np.random.default_rng(515)
    physics = BreakoutPhysics()
    physics.new_game(builtin_level(1, rng), rng)
    for brick in physics.bricks:
        brick.alive = False
        brick.hits = 0
    physics.bricks_alive = 0
    assert render_frame(physics, observation_version="v1").shape[0] == 7
    assert render_frame(physics, observation_version="v2").shape[0] == 8
