from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
import numpy as np

from rl.dqn.agent import DQNAgent
from rl.train import (
    apply_plateau_adjustment,
    create_run_dir,
    main as train_main,
    paired_gate_decision,
    save_checkpoint,
    should_expand_gate,
)


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

    def test_optimizer_reset_keeps_rate_and_discards_moments(self) -> None:
        agent = DQNAgent(torch.device("cpu"), seed=1, learning_rate=3e-5)
        parameter = next(agent.online.parameters())
        agent.optimizer.state[parameter]["step"] = torch.tensor(7.0)
        self.assertTrue(agent.optimizer.state)

        agent.reset_optimizer()

        self.assertEqual(agent.learning_rate, 3e-5)
        self.assertFalse(agent.optimizer.state)

    def test_checkpoint_records_evaluation_count(self) -> None:
        agent = DQNAgent(torch.device("cpu"), seed=1)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            run_dir.mkdir()
            path = run_dir / "latest.pt"
            save_checkpoint(
                path,
                agent,
                456.0,
                run_dir,
                eval_episodes=10,
                paddle_hit_reward=0.05,
                adaptive_patience_steps=2_000_000,
                adaptive_adjustments=1,
                last_improvement_step=123_000,
                stall_paddle_hits=10,
                stall_penalty=0.1,
                learn_every=4,
                anchor_steps=50_000,
                anchor_fraction=0.25,
                champion_eval_episodes=50,
                gate_attempts=2,
                n_step=3,
                priority_alpha=0.6,
                champion_max_eval_episodes=200,
            )
            checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.assertEqual(checkpoint["eval_episodes"], 10)
        self.assertEqual(checkpoint["learning_rate"], 1e-4)
        self.assertEqual(checkpoint["gradient_clip_norm"], 10.0)
        self.assertEqual(checkpoint["paddle_hit_reward"], 0.05)
        self.assertEqual(checkpoint["adaptive_patience_steps"], 2_000_000)
        self.assertEqual(checkpoint["adaptive_adjustments"], 1)
        self.assertEqual(checkpoint["last_improvement_step"], 123_000)
        self.assertEqual(checkpoint["stall_paddle_hits"], 10)
        self.assertEqual(checkpoint["stall_penalty"], 0.1)
        self.assertEqual(checkpoint["learn_every"], 4)
        self.assertEqual(checkpoint["anchor_steps"], 50_000)
        self.assertEqual(checkpoint["anchor_fraction"], 0.25)
        self.assertEqual(checkpoint["champion_eval_episodes"], 50)
        self.assertEqual(checkpoint["gate_attempts"], 2)
        self.assertEqual(checkpoint["n_step"], 3)
        self.assertEqual(checkpoint["priority_alpha"], 0.6)
        self.assertEqual(checkpoint["champion_max_eval_episodes"], 200)
        self.assertEqual(checkpoint["run_dir"], str(run_dir))

    def test_paired_champion_gate_requires_confident_improvement(self) -> None:
        incumbent = np.full(50, 500.0)
        strong = paired_gate_decision(np.full(50, 550.0), incumbent)
        self.assertTrue(strong["accepted"])
        self.assertEqual(strong["lower_confidence"], 50.0)

        identical = paired_gate_decision(incumbent.copy(), incumbent)
        self.assertFalse(identical["accepted"])

        noisy_differences = np.asarray([200.0, -100.0] * 25)
        noisy = paired_gate_decision(incumbent + noisy_differences, incumbent)
        self.assertGreater(noisy["mean_difference"], 0)
        self.assertFalse(noisy["accepted"])
        self.assertTrue(should_expand_gate(noisy, 50, 200))
        self.assertFalse(should_expand_gate(noisy, 200, 200))
        self.assertFalse(should_expand_gate(identical, 50, 200))

    def test_strong_initial_gate_must_expand_before_promotion(self) -> None:
        incumbent = np.full(50, 500.0)
        strong = paired_gate_decision(np.full(50, 550.0), incumbent)

        self.assertTrue(strong["accepted"])
        self.assertTrue(should_expand_gate(strong, 50, 200))
        self.assertFalse(should_expand_gate(strong, 200, 200))

    def test_gradient_clipping_is_applied_and_legacy_checkpoints_stay_unclipped(self) -> None:
        agent = DQNAgent(torch.device("cpu"), seed=1, gradient_clip_norm=10.0)

        class ZeroReplay:
            def sample(self, batch_size, device, rng):
                del rng
                return (
                    torch.zeros((batch_size, 78), device=device),
                    torch.zeros(batch_size, dtype=torch.long, device=device),
                    torch.zeros(batch_size, device=device),
                    torch.zeros((batch_size, 78), device=device),
                    torch.ones(batch_size, device=device),
                )

        with patch("torch.nn.utils.clip_grad_norm_") as clip:
            agent.learn(ZeroReplay())
        clip.assert_called_once()
        self.assertEqual(clip.call_args.args[1], 10.0)

        legacy_checkpoint = agent.checkpoint(best_eval_score=1.0)
        legacy_checkpoint.pop("gradient_clip_norm")
        resumed = DQNAgent(torch.device("cpu"), seed=2)
        resumed.load_checkpoint(legacy_checkpoint)
        self.assertIsNone(resumed.gradient_clip_norm)

    def test_plateau_adjustment_cools_learning_and_reheats_exploration(self) -> None:
        agent = DQNAgent(torch.device("cpu"), seed=1, learning_rate=3e-5)
        agent.agent_steps = 200_000
        self.assertAlmostEqual(agent.epsilon, 0.05)

        old_lr, new_lr = apply_plateau_adjustment(agent)
        self.assertEqual(old_lr, 3e-5)
        self.assertEqual(new_lr, 1.5e-5)
        self.assertAlmostEqual(agent.epsilon, 0.15)

        agent.agent_steps += 50_000
        self.assertAlmostEqual(agent.epsilon, 0.10)
        checkpoint = agent.checkpoint(best_eval_score=10.0)
        resumed = DQNAgent(torch.device("cpu"), seed=2)
        resumed.load_checkpoint(checkpoint)
        self.assertAlmostEqual(resumed.epsilon, 0.10)

        resumed.agent_steps += 50_000
        self.assertAlmostEqual(resumed.epsilon, 0.05)
        resumed.set_learning_rate(1e-5)
        _, floor_lr = apply_plateau_adjustment(resumed)
        self.assertEqual(floor_lr, 1e-5)

    def test_gamma_and_epsilon_decay_round_trip_with_legacy_defaults(self) -> None:
        agent = DQNAgent(
            torch.device("cpu"), seed=1, gamma=0.997, epsilon_decay_steps=500_000
        )
        self.assertEqual(agent.gamma, 0.997)
        self.assertEqual(agent.epsilon_decay_steps, 500_000)
        checkpoint = agent.checkpoint(best_eval_score=1.0)
        self.assertEqual(checkpoint["gamma"], 0.997)
        self.assertEqual(checkpoint["epsilon_decay_steps"], 500_000)

        resumed = DQNAgent(torch.device("cpu"), seed=2)  # constructed with defaults
        resumed.load_checkpoint(checkpoint)
        self.assertEqual(resumed.gamma, 0.997)
        self.assertEqual(resumed.epsilon_decay_steps, 500_000)

        legacy = agent.checkpoint(best_eval_score=1.0)
        legacy.pop("gamma")
        legacy.pop("epsilon_decay_steps")
        legacy_resumed = DQNAgent(torch.device("cpu"), seed=3)
        legacy_resumed.load_checkpoint(legacy)
        self.assertEqual(legacy_resumed.gamma, 0.99)
        self.assertEqual(legacy_resumed.epsilon_decay_steps, 150_000)

    def test_epsilon_decay_steps_stretches_the_exploration_schedule(self) -> None:
        fast = DQNAgent(torch.device("cpu"), seed=1)  # 150k default
        slow = DQNAgent(torch.device("cpu"), seed=1, epsilon_decay_steps=500_000)
        fast.agent_steps = 150_000
        slow.agent_steps = 150_000
        self.assertAlmostEqual(fast.epsilon, 0.05)  # fully decayed
        self.assertAlmostEqual(slow.epsilon, 1.0 + 0.3 * (0.05 - 1.0))  # 30% along, still exploring

    def test_invalid_gamma_and_epsilon_decay_are_rejected(self) -> None:
        for bad in (0.0, 1.0, -0.5, float("nan")):
            with self.subTest(gamma=bad):
                with self.assertRaises(ValueError):
                    DQNAgent(torch.device("cpu"), gamma=bad)
        with self.assertRaises(ValueError):
            DQNAgent(torch.device("cpu"), epsilon_decay_steps=0)

    def test_save_checkpoint_records_curriculum_fraction(self) -> None:
        agent = DQNAgent(
            torch.device("cpu"), seed=1, gamma=0.997, epsilon_decay_steps=500_000
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            run_dir.mkdir()
            path = run_dir / "latest.pt"
            save_checkpoint(
                path,
                agent,
                1.0,
                run_dir,
                eval_episodes=5,
                curriculum_clear_max=0.6,
            )
            checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.assertEqual(checkpoint["curriculum_clear_max"], 0.6)
        self.assertEqual(checkpoint["gamma"], 0.997)
        self.assertEqual(checkpoint["epsilon_decay_steps"], 500_000)

    def _write_default_checkpoint(self, run_dir: Path) -> Path:
        agent = DQNAgent(torch.device("cpu"), seed=1)  # gamma 0.99, curriculum 0.0
        path = run_dir / "latest.pt"
        save_checkpoint(path, agent, 1.0, run_dir, eval_episodes=5)
        return path

    def test_changing_gamma_on_plain_resume_requires_fork(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_default_checkpoint(Path(temp_dir))
            argv = [
                "train.py", "--resume", str(path), "--gamma", "0.5",
                "--device", "cpu", "--steps", "1000",
            ]
            with patch("sys.argv", argv), self.assertRaises(SystemExit):
                train_main()

    def test_changing_curriculum_on_plain_resume_requires_fork(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_default_checkpoint(Path(temp_dir))
            argv = [
                "train.py", "--resume", str(path), "--curriculum-clear-max", "0.5",
                "--device", "cpu", "--steps", "1000",
            ]
            with patch("sys.argv", argv), self.assertRaises(SystemExit):
                train_main()

    def test_save_checkpoint_records_curriculum_prob(self) -> None:
        agent = DQNAgent(torch.device("cpu"), seed=1)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            run_dir.mkdir()
            path = run_dir / "latest.pt"
            save_checkpoint(
                path,
                agent,
                1.0,
                run_dir,
                eval_episodes=5,
                curriculum_clear_max=0.4,
                curriculum_prob=0.5,
            )
            checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.assertEqual(checkpoint["curriculum_clear_max"], 0.4)
        self.assertEqual(checkpoint["curriculum_prob"], 0.5)

    def test_changing_curriculum_prob_on_plain_resume_requires_fork(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_default_checkpoint(Path(temp_dir))
            argv = [
                "train.py", "--resume", str(path), "--curriculum-prob", "0.5",
                "--device", "cpu", "--steps", "1000",
            ]
            with patch("sys.argv", argv), self.assertRaises(SystemExit):
                train_main()

    def test_run_directory_collision_gets_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "20260720-010203").mkdir()
            (root / "20260720-010203-2").mkdir()
            result = create_run_dir(root, timestamp="20260720-010203")
        self.assertEqual(result.name, "20260720-010203-3")


if __name__ == "__main__":
    unittest.main()
