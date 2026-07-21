from __future__ import annotations

import unittest

import numpy as np

from rl.dqn.n_step import NStepAccumulator


class NStepAccumulatorTests(unittest.TestCase):
    def test_accumulates_rewards_and_flushes_terminal_tail(self) -> None:
        accumulator = NStepAccumulator(3, 0.5)
        observations = [np.asarray([value], dtype=np.float32) for value in range(6)]

        self.assertEqual(
            accumulator.add(observations[0], 1, 1.0, observations[1], False, anchor=True),
            [],
        )
        self.assertEqual(
            accumulator.add(observations[1], 2, 2.0, observations[2], False, anchor=True),
            [],
        )
        first = accumulator.add(
            observations[2], 0, 3.0, observations[3], False, anchor=True
        )
        self.assertEqual(len(first), 1)
        self.assertAlmostEqual(first[0].reward, 2.75)
        self.assertAlmostEqual(first[0].discount, 0.125)
        np.testing.assert_array_equal(first[0].next_observation, observations[3])
        self.assertTrue(first[0].anchor)
        self.assertFalse(first[0].done)

        second = accumulator.add(
            observations[3], 1, 4.0, observations[4], False
        )
        self.assertEqual(len(second), 1)
        self.assertAlmostEqual(second[0].reward, 4.5)
        terminal = accumulator.add(
            observations[4], 2, 5.0, observations[5], True
        )
        self.assertEqual([round(item.reward, 3) for item in terminal], [6.25, 6.5, 5.0])
        self.assertEqual([item.done for item in terminal], [True, True, True])
        self.assertEqual(
            [round(item.discount, 3) for item in terminal],
            [0.125, 0.25, 0.5],
        )
