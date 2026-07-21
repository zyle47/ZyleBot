"""Terminal-safe n-step return accumulation for replay insertion."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class NStepTransition:
    observation: np.ndarray
    action: int
    reward: float
    next_observation: np.ndarray
    done: bool
    discount: float
    anchor: bool


class NStepAccumulator:
    def __init__(self, steps: int, gamma: float) -> None:
        if steps <= 0:
            raise ValueError("steps must be positive")
        if not 0.0 < gamma <= 1.0:
            raise ValueError("gamma must be in (0, 1]")
        self.steps = steps
        self.gamma = gamma
        self.pending: deque[tuple[np.ndarray, int, float, np.ndarray, bool, bool]] = deque()

    def _emit(self) -> NStepTransition:
        horizon = min(self.steps, len(self.pending))
        reward = 0.0
        next_observation = self.pending[0][3]
        done = False
        actual_horizon = 0
        for index, transition in enumerate(self.pending):
            if index >= horizon:
                break
            reward += self.gamma**index * transition[2]
            next_observation = transition[3]
            done = transition[4]
            actual_horizon = index + 1
            if done:
                break
        first = self.pending.popleft()
        return NStepTransition(
            observation=first[0],
            action=first[1],
            reward=reward,
            next_observation=next_observation,
            done=done,
            discount=self.gamma**actual_horizon,
            anchor=first[5],
        )

    def add(
        self,
        observation: np.ndarray,
        action: int,
        reward: float,
        next_observation: np.ndarray,
        done: bool,
        *,
        anchor: bool = False,
    ) -> list[NStepTransition]:
        self.pending.append(
            (observation, action, reward, next_observation, done, anchor)
        )
        emitted: list[NStepTransition] = []
        if done:
            while self.pending:
                emitted.append(self._emit())
        elif len(self.pending) >= self.steps:
            emitted.append(self._emit())
        return emitted
