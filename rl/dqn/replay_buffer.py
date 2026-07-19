"""A compact, preallocated replay ring buffer."""

from __future__ import annotations

import numpy as np
import torch


class ReplayBuffer:
    """Uniform replay breaks the strong time correlation in online trajectories."""

    def __init__(self, capacity: int = 500_000, observation_size: int = 78) -> None:
        self.capacity = capacity
        self.observations = np.empty((capacity, observation_size), dtype=np.float32)
        self.next_observations = np.empty((capacity, observation_size), dtype=np.float32)
        self.actions = np.empty(capacity, dtype=np.int64)
        self.rewards = np.empty(capacity, dtype=np.float32)
        self.dones = np.empty(capacity, dtype=np.float32)
        self.position = 0
        self.size = 0

    def __len__(self) -> int:
        return self.size

    def add(self, observation, action: int, reward: float, next_observation, done: bool) -> None:
        index = self.position
        self.observations[index] = observation
        self.actions[index] = action
        self.rewards[index] = reward
        self.next_observations[index] = next_observation
        self.dones[index] = float(done)
        self.position = (self.position + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

    def sample(self, batch_size: int, device: torch.device, rng: np.random.Generator):
        if self.size < batch_size:
            raise ValueError(f"need {batch_size} transitions, only {self.size} available")
        indices = rng.integers(0, self.size, size=batch_size)
        return (
            torch.as_tensor(self.observations[indices], device=device),
            torch.as_tensor(self.actions[indices], device=device),
            torch.as_tensor(self.rewards[indices], device=device),
            torch.as_tensor(self.next_observations[indices], device=device),
            torch.as_tensor(self.dones[indices], device=device),
        )
