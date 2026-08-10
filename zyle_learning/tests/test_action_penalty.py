from __future__ import annotations

import pytest

from zl.env.breakout import BreakoutEnv


def _run(actions: list[int], penalty: float) -> tuple[float, int]:
    env = BreakoutEnv(level_mode="fixed", level=1, action_change_penalty=penalty)
    env.reset(seed=2024)
    total = 0.0
    info: dict = {}
    for action in actions:
        _, reward, terminated, truncated, info = env.step(action)
        total += reward
        if terminated or truncated:
            break
    return total, int(info["action_changes"])


def test_disabled_by_default_and_counts_are_still_reported() -> None:
    steady, changes_steady = _run([2] * 12, 0.0)
    flipping, changes_flip = _run([1, 2] * 6, 0.0)
    assert changes_steady == 0
    assert changes_flip == 11
    # With the penalty off, restlessness must cost exactly nothing.
    assert steady == pytest.approx(flipping, abs=1e-9)


def test_penalty_charges_once_per_flip_only() -> None:
    penalty = 0.01
    base_steady, _ = _run([2] * 12, 0.0)
    steady, changes = _run([2] * 12, penalty)
    assert changes == 0
    assert steady == pytest.approx(base_steady, abs=1e-9)

    base_flip, _ = _run([1, 2] * 6, 0.0)
    flip, flips = _run([1, 2] * 6, penalty)
    assert flips == 11
    assert flip == pytest.approx(base_flip - 11 * penalty, abs=1e-9)


def test_holding_still_is_not_penalized() -> None:
    penalty = 0.01
    base, _ = _run([0] * 10, 0.0)
    held, changes = _run([0] * 10, penalty)
    assert changes == 0
    assert held == pytest.approx(base, abs=1e-9)


def test_counter_resets_between_episodes() -> None:
    env = BreakoutEnv(level_mode="fixed", level=1, action_change_penalty=0.01)
    env.reset(seed=5)
    for action in (1, 2, 1, 2):
        env.step(action)
    assert env.action_changes == 3
    _, info = env.reset(seed=5)
    assert env.action_changes == 0
    assert info["action_changes"] == 0
    # The first action of a new episode has no predecessor, so it is never a "change".
    _, _, _, _, info = env.step(2)
    assert info["action_changes"] == 0


def test_negative_penalty_is_rejected() -> None:
    with pytest.raises(ValueError):
        BreakoutEnv(level_mode="fixed", level=1, action_change_penalty=-0.1)


def test_recommended_scale_stays_small_against_a_full_clear() -> None:
    """Guards the real risk: a penalty large enough to outweigh winning.

    A full clear is worth ~4.0 (bricks 1.0 + clear bonus 2.0 + progression 1.0). Episodes
    run thousands of decisions with ~50% flips, so the per-flip cost must stay tiny.
    """
    flips_per_episode = 1500
    assert 0.0005 * flips_per_episode < 1.0
    assert 0.01 * flips_per_episode > 4.0, "0.01 would dominate the objective - do not use"
