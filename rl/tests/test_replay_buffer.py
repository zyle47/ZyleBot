from __future__ import annotations

import unittest

import numpy as np
import torch

from rl.dqn.replay_buffer import AnchoredReplayBuffer


class AnchoredReplayBufferTests(unittest.TestCase):
    def test_protected_fraction_is_sampled_without_online_overwrite(self) -> None:
        replay = AnchoredReplayBuffer(
            capacity=1_000,
            observation_size=4,
            anchor_capacity=200,
            anchor_fraction=0.25,
        )
        zero = np.zeros(4, dtype=np.float32)
        one = np.ones(4, dtype=np.float32)
        for _ in range(200):
            replay.add_anchor(one, 1, 1.0, one, False)
        for _ in range(800):
            replay.add(zero, 0, 0.0, zero, False)

        observations, _, rewards, _, _ = replay.sample(
            100,
            torch.device("cpu"),
            np.random.default_rng(7),
        )

        self.assertEqual(int(rewards.sum().item()), 25)
        self.assertEqual(int(observations.sum().item()), 25 * 4)
        self.assertEqual(len(replay.anchor), 200)
        self.assertEqual(len(replay.online), 800)

    def test_sum_tree_sampling_favors_high_td_error(self) -> None:
        replay = AnchoredReplayBuffer(
            capacity=1_000,
            observation_size=4,
            anchor_capacity=200,
            anchor_fraction=0.0,
            priority_alpha=1.0,
        )
        observation = np.zeros(4, dtype=np.float32)
        for index in range(800):
            replay.add(observation, 0, float(index == 0), observation, False)
        replay.online.update_priorities(np.asarray([0]), np.asarray([1_000.0]))

        high_priority_samples = 0
        rng = np.random.default_rng(9)
        for _ in range(50):
            sample = replay.online.sample_prioritized(
                100, torch.device("cpu"), rng, beta=0.4
            )
            high_priority_samples += int(np.count_nonzero(sample[7] == 0))

        self.assertGreater(high_priority_samples, 2_500)


if __name__ == "__main__":
    unittest.main()
