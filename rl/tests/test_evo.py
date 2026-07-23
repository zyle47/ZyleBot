from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.rl_policy import NumpyPolicy
from rl.evo import genome as g
from rl.evo.evaluate import (
    NO_CURRICULUM,
    _fitness_worker,
    genome_fitness,
    rollout_score,
)
from rl.evo.population import (
    EvoConfig,
    crossover,
    init_population,
    mutate,
    next_generation,
    tournament_select,
)

CHAMPION_PATH = Path("rl/policy/breakout_policy.npz")


class GenomeCodecTests(unittest.TestCase):
    def test_genome_size_matches_mlp(self) -> None:
        # 78*256 + 256 + 256*256 + 256 + 3*256 + 3
        self.assertEqual(g.GENOME_SIZE, 86_787)

    def test_flatten_unflatten_roundtrip(self) -> None:
        rng = np.random.default_rng(1)
        arrays = {name: rng.standard_normal(shape).astype(np.float32) for name, shape in g.ARRAY_SPECS}
        restored = g.unflatten(g.flatten(arrays))
        for name, _ in g.ARRAY_SPECS:
            np.testing.assert_array_equal(restored[name], arrays[name])

    def test_unflatten_rejects_wrong_length(self) -> None:
        with self.assertRaises(ValueError):
            g.unflatten(np.zeros(g.GENOME_SIZE - 1, dtype=np.float32))

    def test_load_champion_shape(self) -> None:
        vector = g.load_champion(CHAMPION_PATH)
        self.assertEqual(vector.shape, (g.GENOME_SIZE,))
        self.assertEqual(vector.dtype, np.float32)

    def test_load_genome_accepts_both_npz_policy_and_npy_genome(self) -> None:
        # Every --champion consumer must use load_genome, not load_champion: a run
        # continued from a previous best passes a raw .npy and np.load returns a
        # bare ndarray (no context manager), which is how the audit path once broke.
        from_npz = g.load_genome(CHAMPION_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "best.npy"
            np.save(raw, from_npz)
            from_npy = g.load_genome(raw)
        np.testing.assert_array_equal(from_npz, from_npy)
        self.assertEqual(from_npy.dtype, np.float32)


class ForwardParityTests(unittest.TestCase):
    """The genome forward pass must equal the app's served policy exactly, so what
    evolves is bit-for-bit what the arena plays."""

    def test_champion_forward_matches_numpy_policy(self) -> None:
        policy = NumpyPolicy(CHAMPION_PATH)
        arrays = g.unflatten(g.load_champion(CHAMPION_PATH))
        rng = np.random.default_rng(3)
        for _ in range(5):
            obs = rng.random(78).astype(np.float32)
            np.testing.assert_allclose(
                g.q_values(arrays, obs), policy.q_values(obs), rtol=0, atol=1e-5
            )

    def test_mutated_genome_roundtrips_through_export(self) -> None:
        from rl.export_policy import publish_policy

        rng = np.random.default_rng(5)
        vector = mutate(g.load_champion(CHAMPION_PATH), 0.05, 1.0, rng)
        arrays = g.unflatten(vector)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "breakout_policy.npz"
            publish_policy(arrays, training_steps=1, eval_score=1.0, output=out)
            served = NumpyPolicy(out)
            for _ in range(5):
                obs = rng.random(78).astype(np.float32)
                self.assertEqual(g.act(arrays, obs), int(np.argmax(served.q_values(obs))))


class OperatorTests(unittest.TestCase):
    def test_init_population_preserves_seed_at_index_zero(self) -> None:
        rng = np.random.default_rng(0)
        seed = rng.standard_normal(g.GENOME_SIZE).astype(np.float32)
        pop = init_population(seed, 10, 0.02, rng)
        self.assertEqual(len(pop), 10)
        np.testing.assert_array_equal(pop[0], seed)
        self.assertFalse(np.array_equal(pop[1], seed))

    def test_mutation_zero_sigma_is_identity(self) -> None:
        rng = np.random.default_rng(0)
        vector = rng.standard_normal(g.GENOME_SIZE).astype(np.float32)
        np.testing.assert_array_equal(mutate(vector, 0.0, 1.0, rng), vector)

    def test_mutation_changes_weights(self) -> None:
        rng = np.random.default_rng(0)
        vector = np.zeros(g.GENOME_SIZE, dtype=np.float32)
        mutated = mutate(vector, 0.1, 1.0, rng)
        self.assertFalse(np.array_equal(mutated, vector))
        self.assertLess(abs(float(mutated.mean())), 0.05)  # zero-mean noise

    def test_crossover_takes_from_both_parents(self) -> None:
        rng = np.random.default_rng(0)
        a = np.zeros(g.GENOME_SIZE, dtype=np.float32)
        b = np.ones(g.GENOME_SIZE, dtype=np.float32)
        child = crossover(a, b, rng)
        self.assertTrue(np.all((child == 0) | (child == 1)))
        self.assertTrue(0 < int(child.sum()) < g.GENOME_SIZE)

    def test_tournament_favors_fitter_individuals(self) -> None:
        # Sampling is with replacement, so selection is statistical, not absolute:
        # the fittest index must dominate, and the least fit must be rare.
        from collections import Counter

        rng = np.random.default_rng(0)
        fitnesses = np.array([1.0, 9.0, 3.0, 2.0])  # best index 1, worst index 0
        counts = Counter(tournament_select(fitnesses, 3, rng) for _ in range(2_000))
        self.assertEqual(counts.most_common(1)[0][0], 1)
        self.assertGreater(counts[1], counts[0] * 5)

    def test_next_generation_carries_elites_unchanged(self) -> None:
        config = EvoConfig(population=6, elite=2, sigma_init=0.1)
        pop = [np.full(g.GENOME_SIZE, i, dtype=np.float32) for i in range(6)]
        fitnesses = np.array([10.0, 50.0, 30.0, 5.0, 40.0, 20.0])
        children, order = next_generation(pop, fitnesses, config, 0.1, np.random.default_rng(0))
        self.assertEqual(len(children), 6)
        # Top two fitnesses are indices 1 (50) and 4 (40).
        np.testing.assert_array_equal(children[0], pop[1])
        np.testing.assert_array_equal(children[1], pop[4])
        self.assertEqual(list(order[:2]), [1, 4])


class RolloutTests(unittest.TestCase):
    def test_rollout_is_deterministic(self) -> None:
        arrays = g.unflatten(g.load_champion(CHAMPION_PATH))
        a = rollout_score(arrays, seed=11, max_steps=2_000)
        b = rollout_score(arrays, seed=11, max_steps=2_000)
        self.assertEqual(a, b)

    def test_champion_scores_positive(self) -> None:
        vector = g.load_champion(CHAMPION_PATH)
        result = genome_fitness(vector, (11, 12, 13), max_steps=4_000)
        self.assertEqual(result.scores.shape, (3,))
        self.assertGreater(result.mean, 50.0)


class FinishingBonusTests(unittest.TestCase):
    """Raw score is ~linear in bricks destroyed, so the last brick pays the same as
    the first and nothing selects for FINISHING. The bonus must concentrate reward
    at the end — and must never leak into validation or audit numbers."""

    def test_bonus_is_zero_when_disabled(self) -> None:
        from rl.evo.evaluate import finishing_bonus

        self.assertEqual(finishing_bonus(1.0, 0.0, 4.0), 0.0)

    def test_bonus_peaks_only_on_a_full_clear(self) -> None:
        from rl.evo.evaluate import finishing_bonus

        self.assertAlmostEqual(finishing_bonus(1.0, 3000.0, 4.0), 3000.0)
        self.assertLess(finishing_bonus(0.9, 3000.0, 4.0), 2000.0)
        self.assertLess(finishing_bonus(0.667, 3000.0, 4.0), 700.0)

    def test_bonus_is_monotonic_and_clamped(self) -> None:
        from rl.evo.evaluate import finishing_bonus

        values = [finishing_bonus(f / 10, 1000.0, 4.0) for f in range(11)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(finishing_bonus(1.5, 1000.0, 4.0), 1000.0)  # clamped
        self.assertEqual(finishing_bonus(-0.2, 1000.0, 4.0), 0.0)

    def test_default_fitness_is_unchanged_raw_score(self) -> None:
        vector = g.load_champion(CHAMPION_PATH)
        seeds = (11, 12)
        plain = genome_fitness(vector, seeds, max_steps=2_000)
        self.assertAlmostEqual(plain.mean, float(plain.scores.mean()))

    def test_bonus_raises_fitness_but_never_the_raw_scores(self) -> None:
        # scores stay raw so validation/audits remain comparable across runs.
        vector = g.load_champion(CHAMPION_PATH)
        seeds = (11, 12, 13)
        plain = genome_fitness(vector, seeds, max_steps=4_000)
        shaped = genome_fitness(vector, seeds, max_steps=4_000, clear_bonus=3000.0)
        np.testing.assert_array_equal(plain.scores, shaped.scores)
        self.assertGreater(shaped.mean, plain.mean)
        self.assertGreaterEqual(shaped.clear_rate, 0.0)
        self.assertLessEqual(shaped.clear_rate, 1.0)


class AimShapingTests(unittest.TestCase):
    """Endgame aiming: with few bricks left, a return should point at what's left
    instead of firing to one side and waiting for a lucky carom."""

    def _env_at_endgame(self, keep_col: int, threshold: int = 12):
        """A board with a single surviving brick in ``keep_col``, ball at centre."""
        from rl.breakout_env import PADDLE_Y, BALL_R, BreakoutEnv

        env = BreakoutEnv(aim_threshold=threshold)
        env.reset(seed=3)
        for brick in env.bricks:
            brick["alive"] = False
            brick["hits"] = 0
        target = env.bricks[keep_col]  # top row, chosen column
        target["alive"] = True
        target["hits"] = 1
        env.bricks_alive = 1
        ball = {"x": 400.0, "y": PADDLE_Y - BALL_R, "vx": 0.0, "vy": -env.speed}
        return env, ball, target

    def test_aiming_toward_the_last_brick_scores_higher_than_away(self) -> None:
        env, ball, target = self._env_at_endgame(keep_col=9)  # far right
        toward = dict(ball, vx=env.speed * 0.9)
        env._record_aim(toward)
        scored_toward = env.aim_score

        env2, ball2, _ = self._env_at_endgame(keep_col=9)
        away = dict(ball2, vx=-env2.speed * 0.9)
        env2._record_aim(away)
        scored_away = env2.aim_score

        self.assertGreater(scored_toward, 0.5)
        self.assertEqual(scored_away, 0.0)
        self.assertGreater(scored_toward, scored_away)

    def test_overhead_brick_rewards_a_vertical_return(self) -> None:
        env, ball, target = self._env_at_endgame(keep_col=5)
        centre = target["x"] + target["w"] / 2.0
        vertical = dict(ball, x=centre, vx=0.0)
        env._record_aim(vertical)
        self.assertAlmostEqual(env.aim_score, 1.0, places=3)

    def test_aim_is_inert_above_the_threshold_and_when_disabled(self) -> None:
        from rl.breakout_env import BreakoutEnv

        full = BreakoutEnv(aim_threshold=12)
        full.reset(seed=3)  # 60 bricks alive, far above the threshold
        full._record_aim({"x": 400.0, "vx": 300.0})
        self.assertEqual(full.aim_contacts, 0)

        off = BreakoutEnv()  # disabled by default
        off.reset(seed=3)
        off.bricks_alive = 1
        off._record_aim({"x": 400.0, "vx": 300.0})
        self.assertEqual(off.aim_contacts, 0)
        self.assertEqual(off.aim_score, 0.0)

    def test_aim_bonus_shapes_fitness_without_touching_raw_scores(self) -> None:
        vector = g.load_champion(CHAMPION_PATH)
        seeds = (11, 12, 13)
        plain = genome_fitness(vector, seeds, max_steps=4_000)
        aimed = genome_fitness(
            vector, seeds, max_steps=4_000, aim_bonus=200.0, aim_threshold=12
        )
        np.testing.assert_array_equal(plain.scores, aimed.scores)
        self.assertGreaterEqual(aimed.mean, plain.mean)


class CurriculumTests(unittest.TestCase):
    """Training fitness may pre-clear the wall so genomes practise the fast late
    game; validation must stay plain so reported scores remain comparable."""

    def setUp(self) -> None:
        self.arrays = g.unflatten(g.load_champion(CHAMPION_PATH))

    def test_no_curriculum_matches_the_default(self) -> None:
        plain = rollout_score(self.arrays, 9, 2_000)
        explicit = rollout_score(self.arrays, 9, 2_000, NO_CURRICULUM)
        self.assertEqual(plain, explicit)

    def test_curriculum_reaches_the_env(self) -> None:
        seeds = (3, 4, 5, 6)
        plain = [rollout_score(self.arrays, s, 2_000)[0] for s in seeds]
        cleared = [rollout_score(self.arrays, s, 2_000, (0.8, 1.0))[0] for s in seeds]
        self.assertNotEqual(plain, cleared)

    def test_curriculum_rollout_is_deterministic(self) -> None:
        first = rollout_score(self.arrays, 7, 2_000, (0.6, 1.0))
        second = rollout_score(self.arrays, 7, 2_000, (0.6, 1.0))
        self.assertEqual(first, second)

    def test_plain_two_tuple_still_means_clear_max_and_prob(self) -> None:
        from rl.evo.evaluate import Curriculum

        legacy = Curriculum(*(0.8, 1.0))
        self.assertEqual((legacy.clear_max, legacy.prob, legacy.clear_min), (0.8, 1.0, 0.0))

    def test_clear_min_concentrates_practice_on_the_endgame(self) -> None:
        # A [0, 0.9) draw mostly yields easy boards; a [0.8, 0.9) band must always
        # start deep into the wall — that is the whole point of the min.
        from rl.breakout_env import BreakoutEnv

        banded = BreakoutEnv(
            curriculum_clear_max=0.9, curriculum_clear_min=0.8, curriculum_prob=1.0
        )
        for seed in range(12):
            banded.reset(seed=seed)
            cleared = 60 - banded.bricks_alive
            self.assertGreaterEqual(cleared, int(0.8 * 60) - 1, f"seed {seed}")
            self.assertLess(cleared, 60)

    def test_clear_min_zero_matches_previous_behaviour(self) -> None:
        from rl.breakout_env import BreakoutEnv

        without = BreakoutEnv(curriculum_clear_max=0.6, curriculum_prob=1.0)
        with_explicit = BreakoutEnv(
            curriculum_clear_max=0.6, curriculum_prob=1.0, curriculum_clear_min=0.0
        )
        for seed in (1, 2, 3):
            without.reset(seed=seed)
            with_explicit.reset(seed=seed)
            self.assertEqual(without.bricks_alive, with_explicit.bricks_alive)

    def test_clear_min_above_max_is_rejected(self) -> None:
        from rl.breakout_env import BreakoutEnv

        with self.assertRaises(ValueError):
            BreakoutEnv(curriculum_clear_max=0.5, curriculum_clear_min=0.7)

    def test_fitness_worker_accepts_both_payload_shapes(self) -> None:
        vector = g.load_champion(CHAMPION_PATH)
        self.assertEqual(
            _fitness_worker((vector, (11,), 1_500)),
            _fitness_worker((vector, (11,), 1_500, NO_CURRICULUM)),
        )


class ValidationSeedRotationTests(unittest.TestCase):
    """Rotating the holdout stops a long run from overfitting one fixed seed set."""

    def test_rotations_give_different_reproducible_seed_sets(self) -> None:
        from rl.evo.evolve import validation_seeds

        first = validation_seeds(2, 32, 0)
        second = validation_seeds(2, 32, 1)
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, second)
        self.assertEqual(first, validation_seeds(2, 32, 0))  # reproducible
        self.assertLess(len(set(first) & set(second)), 4)  # essentially disjoint

    def test_default_rotation_matches_the_unrotated_set(self) -> None:
        from rl.evo.evolve import validation_seeds

        self.assertEqual(validation_seeds(2, 16), validation_seeds(2, 16, 0))


class CheckpointConversionTests(unittest.TestCase):
    """The evolved genome must convert into a torch checkpoint the DQN trainer can
    resume — and the converted agent must play *identically*, or gradient
    fine-tuning would not actually start from the evolved brain."""

    def test_state_dict_keys_match_the_qnetwork(self) -> None:
        from rl.dqn.network import QNetwork
        from rl.evo.to_checkpoint import genome_to_state_dict

        state = genome_to_state_dict(g.unflatten(g.load_champion(CHAMPION_PATH)))
        self.assertEqual(set(state), set(QNetwork().state_dict()))

    def test_converted_agent_plays_identically_to_the_genome(self) -> None:
        import torch

        from rl.evo.to_checkpoint import agent_from_genome

        vector = g.load_champion(CHAMPION_PATH)
        arrays = g.unflatten(vector)
        agent = agent_from_genome(vector, torch.device("cpu"), 3_050_000)
        self.assertEqual(agent.agent_steps, 3_050_000)
        rng = np.random.default_rng(11)
        for _ in range(20):
            obs = rng.random(78).astype(np.float32)
            self.assertEqual(agent.act(obs, greedy=True), g.act(arrays, obs))


if __name__ == "__main__":
    unittest.main()
