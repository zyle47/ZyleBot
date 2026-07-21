from __future__ import annotations

import math
import unittest

import numpy as np

from rl.breakout_env import (
    BALL_BASE_SPEED,
    BALL_R,
    BRICK_SIDE,
    BRICK_TOP,
    MAX_SPEED,
    PADDLE_Y,
    PHYSICS_STEP,
    SPEED_PER_BRICK,
    BreakoutEnv,
)
from rl.features import build_observation


class BreakoutEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = BreakoutEnv()
        self.env.reset(seed=7)

    def test_level_one_wall_geometry_and_points(self) -> None:
        bricks = self.env.bricks
        self.assertEqual(len(bricks), 60)
        self.assertEqual([bricks[row * 10]["points"] for row in range(6)], [70, 70, 50, 50, 30, 30])
        self.assertAlmostEqual(bricks[0]["x"], BRICK_SIDE)
        self.assertAlmostEqual(bricks[0]["y"], BRICK_TOP)
        self.assertAlmostEqual(bricks[0]["w"], 70.6)
        self.assertAlmostEqual(bricks[-1]["x"] + bricks[-1]["w"], 800 - BRICK_SIDE)
        self.assertAlmostEqual(bricks[-1]["y"] + bricks[-1]["h"], 222.0)

    def test_paddle_deflection_offsets(self) -> None:
        for offset, expected_angle in ((-1, -math.pi / 3), (0, 0), (1, math.pi / 3)):
            with self.subTest(offset=offset):
                self.env.reset(seed=7)
                x = self.env.paddle["x"] + offset * self.env.paddle["w"] / 2
                self.env.balls = [{"x": x, "y": PADDLE_Y - BALL_R - 1, "vx": 0.0, "vy": 240.0}]
                self.env._physics_step(0, 1 / 240)
                ball = self.env.balls[0]
                self.assertAlmostEqual(math.hypot(ball["vx"], ball["vy"]), self.env.speed, places=6)
                actual_angle = math.atan2(ball["vx"], -ball["vy"])
                self.assertAlmostEqual(actual_angle, expected_angle, places=6)

    def test_enforce_bounce_normalizes_clamps_and_preserves_signs(self) -> None:
        for vx, vy in ((-500.0, -1.0), (500.0, 1.0), (-20.0, 900.0)):
            with self.subTest(vx=vx, vy=vy):
                ball = {"vx": vx, "vy": vy}
                self.env.enforce_bounce(ball)
                self.assertAlmostEqual(math.hypot(ball["vx"], ball["vy"]), self.env.speed)
                self.assertGreaterEqual(abs(ball["vy"]), 0.25 * self.env.speed)
                self.assertEqual(math.copysign(1, ball["vx"]), math.copysign(1, vx))
                self.assertEqual(math.copysign(1, ball["vy"]), math.copysign(1, vy))

    def test_brick_side_and_vertical_resolution(self) -> None:
        target = self.env.bricks[25]
        cases = (
            ({"x": target["x"] - BALL_R - 1, "y": target["y"] + target["h"] / 2, "vx": 300.0, "vy": 90.0}, "vx"),
            ({"x": target["x"] + target["w"] / 2, "y": target["y"] - BALL_R - 1, "vx": 90.0, "vy": 300.0}, "vy"),
        )
        for initial, reflected_axis in cases:
            with self.subTest(axis=reflected_axis):
                self.env.reset(seed=7)
                target = self.env.bricks[25]
                raw = self.env.get_raw_state()
                raw["bricks"][:] = [target]
                self.env.set_raw_state(raw)
                self.env.balls = [dict(initial)]
                before = self.env.balls[0][reflected_axis]
                self.env._physics_step(0, PHYSICS_STEP)
                ball = self.env.balls[0]
                self.assertLess(before * ball[reflected_axis], 0)
                closest_x = max(target["x"], min(ball["x"], target["x"] + target["w"]))
                closest_y = max(target["y"], min(ball["y"], target["y"] + target["h"]))
                self.assertGreaterEqual((ball["x"] - closest_x) ** 2 + (ball["y"] - closest_y) ** 2, BALL_R**2)

    def test_scoring_speed_ramp_and_cap(self) -> None:
        brick = self.env.bricks[0]
        self.env.balls = [{"x": brick["x"] + brick["w"] / 2, "y": brick["y"] - BALL_R - 1, "vx": 0.0, "vy": 300.0}]
        scored, _, _, _ = self.env._physics_step(0, PHYSICS_STEP)
        self.assertEqual(scored, 70)
        self.assertEqual(self.env.score, 70)
        self.assertEqual(self.env.speed, 345)

        self.env.speed = MAX_SPEED
        second = self.env.bricks[1]
        self.env.balls = [{"x": second["x"] + second["w"] / 2, "y": second["y"] - BALL_R - 1, "vx": 0.0, "vy": 300.0}]
        self.env._physics_step(0, PHYSICS_STEP)
        self.assertEqual(self.env.speed, MAX_SPEED)

    def test_paddle_hit_shaping_rewards_each_real_collision_once(self) -> None:
        shaped = BreakoutEnv(paddle_hit_reward=0.05)
        shaped.reset(seed=7)
        shaped.balls = [
            {
                "x": shaped.paddle["x"],
                "y": PADDLE_Y - BALL_R - 1,
                "vx": 0.0,
                "vy": 240.0,
            }
        ]
        _, reward, terminated, truncated, info = shaped.step(0)

        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertAlmostEqual(reward, 0.05)
        self.assertEqual(info["paddle_hits"], 1)
        self.assertEqual(info["episode_paddle_hits"], 1)
        self.assertEqual(info["score"], 0)
        self.assertLess(shaped.balls[0]["vy"], 0)

        unshaped = BreakoutEnv()
        unshaped.reset(seed=7)
        unshaped.balls = [
            {
                "x": unshaped.paddle["x"],
                "y": PADDLE_Y - BALL_R - 1,
                "vx": 0.0,
                "vy": 240.0,
            }
        ]
        _, legacy_reward, _, _, legacy_info = unshaped.step(0)
        self.assertEqual(legacy_reward, 0.0)
        self.assertEqual(legacy_info["paddle_hits"], 1)

    def test_stall_penalty_cancels_ten_hit_bonus_and_brick_resets_streak(self) -> None:
        env = BreakoutEnv(
            paddle_hit_reward=0.01,
            stall_paddle_hits=10,
            stall_penalty=0.1,
        )
        env.reset(seed=7)
        total_reward = 0.0
        for hit in range(1, 11):
            env.balls = [
                {
                    "x": env.paddle["x"],
                    "y": PADDLE_Y - BALL_R - 1,
                    "vx": 0.0,
                    "vy": 240.0,
                }
            ]
            _, reward, _, _, info = env.step(0)
            total_reward += reward
            self.assertEqual(info["stall_penalties"], int(hit == 10))

        self.assertAlmostEqual(total_reward, 0.0)
        self.assertEqual(info["paddle_hits_without_brick"], 0)
        self.assertEqual(info["episode_stall_penalties"], 1)

        for _ in range(3):
            env.balls = [
                {
                    "x": env.paddle["x"],
                    "y": PADDLE_Y - BALL_R - 1,
                    "vx": 0.0,
                    "vy": 240.0,
                }
            ]
            env.step(0)
        self.assertEqual(env.paddle_hits_without_brick, 3)

        brick = env.bricks[0]
        env.balls = [
            {
                "x": brick["x"] + brick["w"] / 2,
                "y": brick["y"] - BALL_R - 1,
                "vx": 0.0,
                "vy": 300.0,
            }
        ]
        env.step(0)
        self.assertEqual(env.paddle_hits_without_brick, 0)

    def test_drain_and_level_clear_terminal_rewards(self) -> None:
        self.env.balls = [{"x": 400.0, "y": 608.0, "vx": 0.0, "vy": 340.0}]
        _, reward, terminated, truncated, info = self.env.step(0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(reward, -5.0)
        self.assertTrue(info["life_lost"])

        self.env.reset(seed=7)
        raw = self.env.get_raw_state()
        target = raw["bricks"][0]
        for brick in raw["bricks"][1:]:
            brick["alive"] = False
            brick["hits"] = 0
        self.env.set_raw_state(raw)
        self.env.balls = [{"x": target["x"] + target["w"] / 2, "y": target["y"] - BALL_R - 1, "vx": 0.0, "vy": 300.0}]
        _, reward, terminated, truncated, info = self.env.step(0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertAlmostEqual(reward, 5.7)
        self.assertTrue(info["level_clear"])

    def test_seeded_trajectories_are_deterministic(self) -> None:
        env_a, env_b, env_c = BreakoutEnv(), BreakoutEnv(), BreakoutEnv()
        obs_a, _ = env_a.reset(seed=123)
        obs_b, _ = env_b.reset(seed=123)
        obs_c, _ = env_c.reset(seed=124)
        np.testing.assert_array_equal(obs_a, obs_b)
        self.assertFalse(np.array_equal(obs_a, obs_c))
        for action in [0, 1, 2, 2, 0, 1] * 20:
            result_a = env_a.step(action)
            result_b = env_b.step(action)
            np.testing.assert_array_equal(result_a[0], result_b[0])
            self.assertEqual(result_a[1:4], result_b[1:4])
            if result_a[2] or result_a[3]:
                break


class CurriculumTests(unittest.TestCase):
    def test_default_env_starts_from_a_full_slow_board(self) -> None:
        env = BreakoutEnv()
        env.reset(seed=7)
        self.assertEqual(env.bricks_alive, 60)
        self.assertEqual(sum(bool(brick["alive"]) for brick in env.bricks), 60)
        self.assertEqual(env.speed, BALL_BASE_SPEED)

    def test_curriculum_preclears_bricks_and_matches_speed(self) -> None:
        env = BreakoutEnv(curriculum_clear_max=0.6)
        for seed in range(25):
            with self.subTest(seed=seed):
                env.reset(seed=seed)
                alive = sum(bool(brick["alive"]) for brick in env.bricks)
                cleared = 60 - alive
                self.assertEqual(env.bricks_alive, alive)
                self.assertGreaterEqual(alive, 1)  # a curriculum board is never pre-cleared
                self.assertLess(cleared, 0.6 * 60)  # within the requested max fraction
                dead_with_zero_hits = sum(
                    1 for brick in env.bricks if not brick["alive"] and brick["hits"] == 0
                )
                self.assertEqual(dead_with_zero_hits, cleared)
                # A partially-cleared board is a faithful fast-ball state: speed reflects removals.
                self.assertEqual(
                    env.speed, min(BALL_BASE_SPEED + cleared * SPEED_PER_BRICK, MAX_SPEED)
                )

    def test_curriculum_actually_removes_bricks_across_seeds(self) -> None:
        env = BreakoutEnv(curriculum_clear_max=0.6)
        cleared = []
        for seed in range(40):
            env.reset(seed=seed)
            cleared.append(60 - sum(bool(brick["alive"]) for brick in env.bricks))
        self.assertGreater(max(cleared), 0)

    def test_curriculum_is_deterministic_per_seed(self) -> None:
        first, second = BreakoutEnv(curriculum_clear_max=0.6), BreakoutEnv(curriculum_clear_max=0.6)
        obs_first, _ = first.reset(seed=99)
        obs_second, _ = second.reset(seed=99)
        np.testing.assert_array_equal(obs_first, obs_second)
        self.assertEqual(first.speed, second.speed)
        self.assertEqual(
            [brick["alive"] for brick in first.bricks],
            [brick["alive"] for brick in second.bricks],
        )

    def test_curriculum_fraction_out_of_range_is_rejected(self) -> None:
        for bad in (-0.1, 1.0, 1.5, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    BreakoutEnv(curriculum_clear_max=bad)

    def test_curriculum_prob_zero_keeps_every_board_full(self) -> None:
        env = BreakoutEnv(curriculum_clear_max=0.6, curriculum_prob=0.0)
        for seed in range(20):
            env.reset(seed=seed)
            self.assertEqual(sum(bool(brick["alive"]) for brick in env.bricks), 60)
            self.assertEqual(env.speed, BALL_BASE_SPEED)

    def test_curriculum_prob_mixes_full_openings_with_cleared_boards(self) -> None:
        # A continuous unseeded RNG stream gives a clean ~Bernoulli(0.5) split.
        env = BreakoutEnv(curriculum_clear_max=0.6, curriculum_prob=0.5)
        env.reset(seed=42)
        full = cleared = 0
        for _ in range(200):
            env.reset()
            if sum(bool(brick["alive"]) for brick in env.bricks) == 60:
                full += 1
            else:
                cleared += 1
        self.assertGreater(full, 60)  # a real share stays full-board openings
        self.assertGreater(cleared, 60)  # a real share are pre-cleared endgames

    def test_curriculum_prob_defaults_to_applying_every_reset(self) -> None:
        env = BreakoutEnv(curriculum_clear_max=0.6)  # curriculum_prob defaults to 1.0
        env.reset(seed=42)
        cleared = 0
        for _ in range(200):
            env.reset()
            if sum(bool(brick["alive"]) for brick in env.bricks) < 60:
                cleared += 1
        self.assertGreater(cleared, 185)  # ~97%; only a near-zero fraction leaves a full board

    def test_curriculum_prob_out_of_range_is_rejected(self) -> None:
        for bad in (-0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    BreakoutEnv(curriculum_clear_max=0.6, curriculum_prob=bad)


class ObservationTests(unittest.TestCase):
    def test_layout_dtype_ranges_sorting_and_wire_parity(self) -> None:
        env = BreakoutEnv()
        _, info = env.reset(seed=42)
        state = info["state"]
        state["balls"] = [
            [100.0, 50.0, -320.0, 320.0],
            [200.0, 500.0, 640.0, -640.0],
        ]
        observation = build_observation(state)
        self.assertEqual(observation.shape, (78,))
        self.assertEqual(observation.dtype, np.float32)
        self.assertTrue(np.all(observation >= -1.0))
        self.assertTrue(np.all(observation <= 1.0))
        self.assertAlmostEqual(observation[1], 200 / 800)
        self.assertAlmostEqual(observation[2], 500 / 600)
        self.assertEqual(observation[5], 1.0)
        self.assertEqual(observation[10], 1.0)
        self.assertEqual(observation[15], 0.0)
        wire = {
            "paddle_x": state["paddle_x"],
            "balls": state["balls"],
            "speed": state["speed"],
            "pierce": state["pierce"],
            "bricks": [(int(hits), int(max_hits)) for hits, max_hits in state["bricks"]],
        }
        np.testing.assert_array_equal(observation, build_observation(wire))


if __name__ == "__main__":
    unittest.main()
