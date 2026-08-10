from __future__ import annotations

import numpy as np

from zl.env.levels import LevelDefinition
from zl.env.physics import Ball, BreakoutPhysics


def _hit(physics: BreakoutPhysics, brick_index: int) -> None:
    brick = physics.bricks[brick_index]
    physics.balls = [Ball(brick.x + brick.w / 2, brick.y + brick.h + 1, 0.0, -340.0)]
    physics.physics_step(0, dt=0.0)


def test_durability_requires_one_collision_per_hit() -> None:
    rng = np.random.default_rng(4)
    physics = BreakoutPhysics()
    physics.new_game(LevelDefinition("durable", 2, 1, ("31",)), rng)
    _hit(physics, 0)
    assert physics.bricks[0].hits == 2
    assert physics.bricks[0].alive


def test_piercer_destroys_durable_brick_without_bouncing() -> None:
    rng = np.random.default_rng(5)
    physics = BreakoutPhysics()
    physics.new_game(LevelDefinition("piercer", 2, 1, ("51",)), rng)
    for _ in range(5):
        _hit(physics, 0)
    assert physics.pierce_remaining == 10.0
    target = physics.bricks[1]
    physics.balls = [Ball(target.x + target.w / 2, target.y + target.h + 1, 0.0, -physics.speed)]
    events = physics.physics_step(0, dt=0.0)
    assert events.bricks_destroyed == 1
    assert not target.alive


def test_splitter_forks_current_ball_into_three() -> None:
    rng = np.random.default_rng(6)
    physics = BreakoutPhysics()
    physics.new_game(LevelDefinition("splitter", 2, 1, ("41",)), rng)
    for _ in range(4):
        _hit(physics, 0)
    assert len(physics.balls) == 3
    assert physics.bricks_alive == 1

