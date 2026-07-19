from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.rl_policy import NumpyPolicy, PolicyUnavailableError


class NumpyPolicyTests(unittest.TestCase):
    @staticmethod
    def _weights() -> dict[str, np.ndarray]:
        rng = np.random.default_rng(1234)
        return {
            "layer1_weight": rng.normal(0, 0.03, (256, 78)).astype(np.float32),
            "layer1_bias": rng.normal(0, 0.01, 256).astype(np.float32),
            "layer2_weight": rng.normal(0, 0.03, (256, 256)).astype(np.float32),
            "layer2_bias": rng.normal(0, 0.01, 256).astype(np.float32),
            "output_weight": rng.normal(0, 0.03, (3, 256)).astype(np.float32),
            "output_bias": rng.normal(0, 0.01, 3).astype(np.float32),
        }

    def test_numpy_forward_matches_fixed_torch_reference(self) -> None:
        # Reference produced with torch.nn.functional.linear in the RL venv.
        expected = np.array([-0.02176768, -0.02536187, -0.00683160], dtype=np.float32)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.npz"
            np.savez(path, **self._weights())
            policy = NumpyPolicy(path)
            actual = policy.q_values(np.linspace(-1, 1, 78, dtype=np.float32))
        np.testing.assert_allclose(actual, expected, atol=1e-5, rtol=1e-5)

    def test_missing_policy_has_specific_error(self) -> None:
        with self.assertRaises(PolicyUnavailableError):
            NumpyPolicy(Path("definitely-missing-policy.npz"))

    def test_act_builds_observation_and_returns_valid_action(self) -> None:
        state = {
            "paddle_x": 400.0,
            "balls": [[400.0, 300.0, 100.0, -300.0]],
            "speed": 340.0,
            "pierce": 0.0,
            "bricks": [(1, 1)] * 60,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.npz"
            np.savez(path, **self._weights())
            action = NumpyPolicy(path).act(state)
        self.assertIn(action, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
