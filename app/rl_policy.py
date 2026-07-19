"""Pure-numpy inference for the exported Breakout DQN policy."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np

from rl.features import build_observation

POLICY_PATH = Path("rl/policy/breakout_policy.npz")
REQUIRED_ARRAYS = (
    "layer1_weight",
    "layer1_bias",
    "layer2_weight",
    "layer2_bias",
    "output_weight",
    "output_bias",
)


class PolicyUnavailableError(RuntimeError):
    """Raised when the exported policy is absent or cannot be validated."""


class NumpyPolicy:
    def __init__(self, path: Path = POLICY_PATH) -> None:
        try:
            with np.load(path, allow_pickle=False) as archive:
                self.weights = {name: archive[name].astype(np.float32) for name in REQUIRED_ARRAYS}
        except (OSError, ValueError, KeyError) as exc:
            raise PolicyUnavailableError(f"Breakout policy unavailable: {exc}") from exc
        expected = {
            "layer1_weight": (256, 78),
            "layer1_bias": (256,),
            "layer2_weight": (256, 256),
            "layer2_bias": (256,),
            "output_weight": (3, 256),
            "output_bias": (3,),
        }
        for name, shape in expected.items():
            if self.weights[name].shape != shape:
                raise PolicyUnavailableError(
                    f"Breakout policy array {name} has shape {self.weights[name].shape}, expected {shape}"
                )

    def q_values(self, observation: np.ndarray) -> np.ndarray:
        x = np.asarray(observation, dtype=np.float32)
        if x.shape != (78,):
            raise ValueError(f"expected observation shape (78,), got {x.shape}")
        x = np.maximum(0.0, x @ self.weights["layer1_weight"].T + self.weights["layer1_bias"])
        x = np.maximum(0.0, x @ self.weights["layer2_weight"].T + self.weights["layer2_bias"])
        return x @ self.weights["output_weight"].T + self.weights["output_bias"]

    def act(self, state: Mapping[str, object]) -> int:
        return int(np.argmax(self.q_values(build_observation(state))))


_policy: NumpyPolicy | None = None


def get_policy() -> NumpyPolicy:
    global _policy
    if _policy is None:
        _policy = NumpyPolicy()
    return _policy


def act(state: Mapping[str, object]) -> int:
    return get_policy().act(state)
