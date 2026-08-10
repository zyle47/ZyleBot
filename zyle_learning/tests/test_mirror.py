from __future__ import annotations

import numpy as np

from zl.env.breakout import BreakoutEnv
from zl.env.mirror import MirrorAugmentation
from zl.env.physics import W


def _always(env: BreakoutEnv) -> MirrorAugmentation:
    return MirrorAugmentation(env, probability=1.0, seed=0)


def test_disabled_augmentation_is_a_passthrough() -> None:
    plain = BreakoutEnv(level_mode="fixed", level=1)
    wrapped = MirrorAugmentation(BreakoutEnv(level_mode="fixed", level=1), probability=0.0, seed=3)
    base, _ = plain.reset(seed=31)
    mirrored, info = wrapped.reset(seed=31)
    assert info["mirrored"] is False
    np.testing.assert_array_equal(base, mirrored)
    for action in (2, 2, 1, 0):
        expected, *_ = plain.step(action)
        actual, *_ = wrapped.step(action)
        np.testing.assert_array_equal(expected, actual)


def test_mirrored_observation_is_the_horizontal_flip() -> None:
    plain = BreakoutEnv(level_mode="fixed", level=3)
    wrapped = _always(BreakoutEnv(level_mode="fixed", level=3))
    base, _ = plain.reset(seed=77)
    mirrored, info = wrapped.reset(seed=77)
    assert info["mirrored"] is True
    np.testing.assert_array_equal(mirrored, base[..., ::-1])
    assert mirrored.shape == base.shape
    assert wrapped.observation_space.contains(mirrored)


def test_mirrored_actions_are_swapped_in_the_real_game() -> None:
    wrapped = _always(BreakoutEnv(level_mode="fixed", level=1))
    wrapped.reset(seed=5)
    start = wrapped.env.physics.paddle_x
    # The policy asks to go LEFT in the mirrored frame; the real paddle must go RIGHT.
    for _ in range(6):
        wrapped.step(1)
    assert wrapped.env.physics.paddle_x > start

    other = _always(BreakoutEnv(level_mode="fixed", level=1))
    other.reset(seed=5)
    start = other.env.physics.paddle_x
    for _ in range(6):
        other.step(2)
    assert other.env.physics.paddle_x < start


def test_mirroring_preserves_the_game_and_is_seeded() -> None:
    first = MirrorAugmentation(BreakoutEnv(level_mode="fixed", level=2), probability=0.5, seed=9)
    second = MirrorAugmentation(BreakoutEnv(level_mode="fixed", level=2), probability=0.5, seed=9)
    flags = []
    for seed in (11, 12, 13, 14):
        _, info_a = first.reset(seed=seed)
        _, info_b = second.reset(seed=seed)
        assert info_a["mirrored"] == info_b["mirrored"]
        flags.append(info_a["mirrored"])
        # Mirroring must not change the underlying game state itself.
        assert first.env.physics.bricks_alive == second.env.physics.bricks_alive
        assert 0.0 <= first.env.physics.paddle_x <= W
    assert any(flags) and not all(flags), "probability 0.5 should produce both orientations"


def test_reward_and_termination_are_untouched_by_mirroring() -> None:
    plain = BreakoutEnv(level_mode="fixed", level=1)
    wrapped = _always(BreakoutEnv(level_mode="fixed", level=1))
    plain.reset(seed=404)
    wrapped.reset(seed=404)
    # Mirrored action 1 (left) is applied as 2 (right) in the real game, so driving the
    # wrapper with swapped actions must reproduce the plain env's rewards exactly.
    for action in (1, 1, 2, 0, 1):
        _, reward_plain, term_plain, trunc_plain, _ = plain.step({0: 0, 1: 2, 2: 1}[action])
        _, reward_mirror, term_mirror, trunc_mirror, _ = wrapped.step(action)
        assert reward_plain == reward_mirror
        assert term_plain == term_mirror and trunc_plain == trunc_mirror
