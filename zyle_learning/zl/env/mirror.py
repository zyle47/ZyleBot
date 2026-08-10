"""Left-right mirror augmentation, so the policy stops being handed.

A CNN policy has no built-in left/right symmetry: whichever direction happened to work
first during training gets reinforced, and the agent develops a persistent lean (ours
drifts left). On a symmetric wall that is harmless, but levels 2-4 are asymmetric, so a
fixed lean means the agent is not steering toward *where the bricks actually are*.

Breakout is exactly mirror-symmetric, so showing the flipped board is free, perfectly
labelled data. Flip the observation horizontally and swap the left/right actions and the
episode is still a legal game -- the policy is forced to become ambidextrous rather than
memorizing one side.

Applied to training environments only; validation and evaluation stay unmirrored so
their numbers remain comparable across runs.
"""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np

# 0 = stay, 1 = left, 2 = right (see BreakoutPhysics.move_paddle).
_SWAP = (0, 2, 1)


class MirrorAugmentation(gym.Wrapper):
    """Randomly mirror whole episodes (decided at reset, held for the episode).

    Mirroring per-episode rather than per-step keeps each trajectory internally
    consistent, so PPO's advantage estimates stay valid.
    """

    def __init__(self, env: gym.Env, probability: float = 0.5, seed: int | None = None) -> None:
        super().__init__(env)
        if not 0.0 <= probability <= 1.0:
            raise ValueError("mirror probability must be in [0, 1]")
        self.probability = float(probability)
        # A dedicated stream so toggling augmentation does not shift the environment's
        # own level/curriculum sampling sequence.
        self._rng = np.random.default_rng(seed)
        self.mirrored = False

    @staticmethod
    def _flip(observation: np.ndarray) -> np.ndarray:
        # Channels are (C, H, W) semantic planes; the width axis is the mirror axis.
        return np.ascontiguousarray(observation[..., ::-1])

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        observation, info = self.env.reset(seed=seed, options=options)
        self.mirrored = bool(self._rng.random() < self.probability)
        if self.mirrored:
            observation = self._flip(observation)
        info["mirrored"] = self.mirrored
        return observation, info

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        # The policy acts in the mirrored frame, so undo the flip before the real env
        # sees the action.
        applied = _SWAP[int(action)] if self.mirrored else int(action)
        observation, reward, terminated, truncated, info = self.env.step(applied)
        if self.mirrored:
            observation = self._flip(observation)
        info["mirrored"] = self.mirrored
        return observation, reward, terminated, truncated, info
