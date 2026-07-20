from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import torch

from rl.dqn.agent import DQNAgent
from rl.train import create_run_dir, save_checkpoint


class TrainingControlTests(unittest.TestCase):
    def test_learning_rate_round_trips_and_can_be_overridden(self) -> None:
        original = DQNAgent(torch.device("cpu"), seed=1, learning_rate=3e-5)
        checkpoint = original.checkpoint(best_eval_score=123.0)
        self.assertEqual(checkpoint["learning_rate"], 3e-5)

        resumed = DQNAgent(torch.device("cpu"), seed=2)
        resumed.load_checkpoint(checkpoint)
        self.assertEqual(resumed.learning_rate, 3e-5)
        resumed.set_learning_rate(1e-5)
        self.assertEqual(resumed.learning_rate, 1e-5)

    def test_checkpoint_records_evaluation_count(self) -> None:
        agent = DQNAgent(torch.device("cpu"), seed=1)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            run_dir.mkdir()
            path = run_dir / "latest.pt"
            save_checkpoint(path, agent, 456.0, run_dir, eval_episodes=10)
            checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.assertEqual(checkpoint["eval_episodes"], 10)
        self.assertEqual(checkpoint["learning_rate"], 1e-4)
        self.assertEqual(checkpoint["run_dir"], str(run_dir))

    def test_run_directory_collision_gets_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "20260720-010203").mkdir()
            (root / "20260720-010203-2").mkdir()
            result = create_run_dir(root, timestamp="20260720-010203")
        self.assertEqual(result.name, "20260720-010203-3")


if __name__ == "__main__":
    unittest.main()
