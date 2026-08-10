from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from zl.env.levels import builtin_level
from zl.env.physics import BreakoutPhysics

FIXTURE = Path(__file__).parent / "fixtures" / "physics_golden.json"


def _assert_state(physics: BreakoutPhysics, expected: dict) -> None:
    assert physics.paddle_x == pytest.approx(expected["paddle_x"], abs=1e-9)
    active_balls = [ball for ball in physics.balls if not ball.dead]
    assert len(active_balls) == len(expected["balls"])
    for actual, wanted in zip(active_balls, expected["balls"], strict=True):
        assert [actual.x, actual.y, actual.vx, actual.vy] == pytest.approx(wanted, abs=1e-9)
    spec = expected["brick_hits"]
    wanted_hits = [int(spec["default"])] * 60
    for index, value in spec["overrides"].items():
        wanted_hits[int(index)] = int(value)
    actual_hits = [brick.hits if brick.alive else 0 for brick in physics.bricks]
    assert actual_hits == wanted_hits
    assert physics.bricks_alive == expected["bricks_alive"]
    assert physics.speed == pytest.approx(expected["speed"], abs=1e-9)
    assert physics.pierce_remaining == pytest.approx(expected["pierce_remaining"], abs=1e-9)
    assert physics.score == expected["score"]
    assert physics.lives == expected["lives"]


def test_level_one_matches_checked_in_golden_transitions() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        rng = np.random.default_rng(case["seed"])
        physics = BreakoutPhysics()
        physics.new_game(builtin_level(1, rng), rng)
        records = {record["after_actions"]: record["state"] for record in case["records"]}
        _assert_state(physics, records[0])
        actions = [action for action, count in case["action_runs"] for _ in range(count)]
        for index, action in enumerate(actions, 1):
            events = physics.decision_step(action)
            if index in records:
                _assert_state(physics, records[index])
            if events.life_lost or events.level_cleared:
                break
        assert max(records) == index


def test_fixture_is_data_only_and_has_multiple_collision_paths() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert "rl.breakout_env" in text
    cases = json.loads(text)["cases"]
    assert len(cases) >= 4
    tracking = next(case for case in cases if case["name"] == "tracking_returns")
    assert tracking["records"][-1]["after_actions"] >= 600
    assert tracking["records"][-1]["state"]["lives"] == 3
    assert tracking["records"][-1]["state"]["score"] >= 800
