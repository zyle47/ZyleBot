from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app import rl_policy
from app.rl_policy import NumpyPolicy, PolicyUnavailableError
from rl.export_policy import publish_policy


def _weights(seed: int = 1234) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {
        "layer1_weight": rng.normal(0, 0.03, (256, 78)).astype(np.float32),
        "layer1_bias": rng.normal(0, 0.01, 256).astype(np.float32),
        "layer2_weight": rng.normal(0, 0.03, (256, 256)).astype(np.float32),
        "layer2_bias": rng.normal(0, 0.01, 256).astype(np.float32),
        "output_weight": rng.normal(0, 0.03, (3, 256)).astype(np.float32),
        "output_bias": rng.normal(0, 0.01, 3).astype(np.float32),
    }


class NumpyPolicyTests(unittest.TestCase):
    def test_numpy_forward_matches_fixed_torch_reference(self) -> None:
        # Reference produced with torch.nn.functional.linear in the RL venv.
        expected = np.array([-0.02176768, -0.02536187, -0.00683160], dtype=np.float32)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.npz"
            np.savez(path, **_weights())
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
            np.savez(path, **_weights())
            action = NumpyPolicy(path).act(state)
        self.assertIn(action, (0, 1, 2))

    def test_legacy_npz_without_scalars_still_loads(self) -> None:
        # Weights-only archive (no in-NPZ metadata, no meta.json): absent version
        # info is accepted as the required version so legacy exports keep working.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.npz"
            np.savez(path, **_weights())
            policy = NumpyPolicy(path)
        self.assertEqual(policy.observation_version, "level1-v1")
        self.assertIsNone(policy.training_steps)
        self.assertIsNone(policy.eval_score)


class AtomicExportTests(unittest.TestCase):
    def test_publish_writes_valid_npz_and_strict_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "breakout_policy.npz"
            meta = publish_policy(_weights(), training_steps=890_000, eval_score=736.0, output=out)

            self.assertTrue(out.exists())
            raw = out.with_name("meta.json").read_text(encoding="utf-8")
            self.assertNotIn("NaN", raw)
            self.assertNotIn("Infinity", raw)
            self.assertEqual(
                json.loads(raw),
                {"observation_version": "level1-v1", "training_steps": 890_000, "eval_score": 736.0},
            )
            self.assertEqual(meta["training_steps"], 890_000)

            policy = NumpyPolicy(out)
            self.assertEqual(policy.observation_version, "level1-v1")
            self.assertEqual(policy.training_steps, 890_000)
            self.assertEqual(policy.eval_score, 736.0)

    def test_non_finite_eval_becomes_null(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "policy.npz"
            meta = publish_policy(_weights(), training_steps=1, eval_score=float("nan"), output=out)
            self.assertIsNone(meta["eval_score"])
            self.assertIsNone(json.loads(out.with_name("meta.json").read_text())["eval_score"])
            self.assertIsNone(NumpyPolicy(out).eval_score)

    def test_npz_scalars_win_over_stale_meta_json(self) -> None:
        # During the two-file replacement window meta.json may lag; the app must
        # pair the fresh weights with the fresh in-NPZ scalars, never stale meta.
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "policy.npz"
            publish_policy(_weights(), training_steps=200, eval_score=80.0, output=out)
            out.with_name("meta.json").write_text(
                json.dumps({"observation_version": "level1-v1", "training_steps": 100, "eval_score": 50.0}),
                encoding="utf-8",
            )
            self.assertEqual(NumpyPolicy(out).training_steps, 200)

    def test_staged_write_failure_keeps_previous_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "breakout_policy.npz"
            publish_policy(_weights(seed=1), training_steps=100, eval_score=50.0, output=out)
            good_bias = NumpyPolicy(out).weights["output_bias"].copy()

            with patch("numpy.savez", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    publish_policy(_weights(seed=2), training_steps=200, eval_score=80.0, output=out)

            after = NumpyPolicy(out)
            self.assertEqual(after.training_steps, 100)  # previous export intact
            np.testing.assert_array_equal(after.weights["output_bias"], good_bias)
            leftovers = [p.name for p in Path(temp_dir).iterdir() if p.name.startswith(".tmp-")]
            self.assertEqual(leftovers, [])  # abandoned temp files cleaned up


class HotReloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "breakout_policy.npz"
        self._orig_path = rl_policy.POLICY_PATH
        rl_policy.POLICY_PATH = self.path
        rl_policy._reset_for_tests()

    def tearDown(self) -> None:
        rl_policy.POLICY_PATH = self._orig_path
        rl_policy._reset_for_tests()
        self._dir.cleanup()

    def _bump_mtime(self) -> None:
        st = self.path.stat()
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))

    @staticmethod
    def _unthrottle() -> None:
        rl_policy._last_stat_check = 0.0

    def test_initial_absence_raises_no_policy(self) -> None:
        with self.assertRaises(PolicyUnavailableError):
            rl_policy.get_policy()

    def test_hot_reload_swaps_to_newer_valid_policy(self) -> None:
        publish_policy(_weights(1), training_steps=100, eval_score=50.0, output=self.path)
        self.assertEqual(rl_policy.get_policy().training_steps, 100)
        publish_policy(_weights(2), training_steps=200, eval_score=80.0, output=self.path)
        self._bump_mtime()
        self._unthrottle()
        self.assertEqual(rl_policy.get_policy().training_steps, 200)

    def test_corrupt_replacement_keeps_last_good(self) -> None:
        publish_policy(_weights(1), training_steps=100, eval_score=50.0, output=self.path)
        self.assertEqual(rl_policy.get_policy().training_steps, 100)
        self.path.write_bytes(b"not a real npz archive")
        self._bump_mtime()
        self._unthrottle()
        self.assertEqual(rl_policy.get_policy().training_steps, 100)

    def test_wrong_shape_replacement_keeps_last_good(self) -> None:
        publish_policy(_weights(1), training_steps=100, eval_score=50.0, output=self.path)
        rl_policy.get_policy()
        bad = _weights(1)
        bad["layer1_weight"] = np.zeros((256, 77), dtype=np.float32)  # wrong shape
        np.savez(self.path, **bad)
        self._bump_mtime()
        self._unthrottle()
        self.assertEqual(rl_policy.get_policy().training_steps, 100)

    def test_wrong_observation_version_keeps_last_good(self) -> None:
        publish_policy(_weights(1), training_steps=100, eval_score=50.0, output=self.path)
        rl_policy.get_policy()
        publish_policy(
            _weights(2),
            training_steps=200,
            eval_score=80.0,
            observation_version="level2-v1",
            output=self.path,
        )
        self._bump_mtime()
        self._unthrottle()
        self.assertEqual(rl_policy.get_policy().training_steps, 100)

    def test_missing_after_load_keeps_last_good(self) -> None:
        publish_policy(_weights(1), training_steps=100, eval_score=50.0, output=self.path)
        rl_policy.get_policy()
        self.path.unlink()
        self._unthrottle()
        self.assertEqual(rl_policy.get_policy().training_steps, 100)


if __name__ == "__main__":
    unittest.main()
