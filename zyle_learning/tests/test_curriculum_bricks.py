from __future__ import annotations

import numpy as np

from zl.env.breakout import BreakoutEnv


def _remaining(level: int, seed: int, **kwargs) -> int:
    env = BreakoutEnv(level_mode="fixed", level=level, **kwargs)
    env.reset(seed=seed)
    return env.physics.bricks_alive


def test_brick_count_curriculum_is_size_independent() -> None:
    """The whole point: level 1 and level 4 must drill the SAME endgame depth."""
    band = dict(curriculum_bricks_min=8, curriculum_bricks_max=24)
    for level in (1, 2, 3, 4):
        for seed in (11, 22, 33):
            assert 8 <= _remaining(level, seed, **band) <= 24


def test_fraction_curriculum_does_not_transfer_across_board_sizes() -> None:
    """Documents the flaw that motivated brick-count mode."""
    fraction = dict(curriculum_clear_min=0.9, curriculum_clear_max=0.9)
    small = _remaining(1, 5, **fraction)
    large = _remaining(4, 5, **fraction)
    assert small <= 6
    assert large > 100
    assert large > small * 10


def test_brick_mode_takes_precedence_and_clamps_to_board_size() -> None:
    # Asking to leave more bricks than exist must not delete the board or crash.
    assert _remaining(1, 7, curriculum_bricks_min=500, curriculum_bricks_max=900) == 60
    # Brick mode wins over an also-specified fraction band.
    remaining = _remaining(
        3, 7, curriculum_clear_min=0.1, curriculum_clear_max=0.2,
        curriculum_bricks_min=5, curriculum_bricks_max=5,
    )
    assert remaining == 5


def test_wide_band_still_gives_small_levels_real_endgames() -> None:
    """A band tuned for level 4 must not starve the 60-brick levels.

    The band is clamped to each board BEFORE drawing, so 8-600 means 8-59 on level 3
    rather than collapsing almost every draw into an untouched full board.
    """
    band = dict(curriculum_bricks_min=8, curriculum_bricks_max=600)
    for level, total in ((1, 60), (2, 62), (3, 59)):
        remaining = [_remaining(level, seed, **band) for seed in range(20)]
        assert all(8 <= r <= total for r in remaining)
        # Most draws must be genuine endgames, not the full board.
        assert sum(r < total for r in remaining) >= 18
    large = [_remaining(4, seed, **band) for seed in range(20)]
    assert all(8 <= r <= 600 for r in large)
    assert max(large) > 100, "level 4 must still reach mid-game states"


def test_brick_curriculum_keeps_a_live_brick_and_is_seeded() -> None:
    kwargs = dict(curriculum_bricks_min=1, curriculum_bricks_max=1)
    assert _remaining(4, 99, **kwargs) == 1
    first = BreakoutEnv(level_mode="fixed", level=2, curriculum_bricks_min=3, curriculum_bricks_max=9)
    second = BreakoutEnv(level_mode="fixed", level=2, curriculum_bricks_min=3, curriculum_bricks_max=9)
    first.reset(seed=123)
    second.reset(seed=123)
    assert first.physics.snapshot() == second.physics.snapshot()
    assert np.hypot(first.physics.balls[0].vx, first.physics.balls[0].vy) == first.physics.speed


def test_probability_gate_still_yields_full_boards() -> None:
    env = BreakoutEnv(
        level_mode="fixed", level=1,
        curriculum_bricks_min=5, curriculum_bricks_max=10, curriculum_probability=0.0,
    )
    _, info = env.reset(seed=404)
    assert info["curriculum_cleared_bricks"] == 0
    assert info["bricks_alive"] == 60


def test_disabled_by_default() -> None:
    assert _remaining(1, 1) == 60
    assert _remaining(4, 1) == 1152
