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
        mean, scores = genome_fitness(vector, (11, 12, 13), max_steps=4_000)
        self.assertEqual(scores.shape, (3,))
        self.assertGreater(mean, 50.0)


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
