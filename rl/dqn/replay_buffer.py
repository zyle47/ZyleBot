"""A compact, preallocated replay ring buffer."""

from __future__ import annotations

import numpy as np
import torch


class ReplayBuffer:
    """Uniform replay breaks the strong time correlation in online trajectories."""

    def __init__(
        self,
        capacity: int = 500_000,
        observation_size: int = 78,
        priority_alpha: float = 0.0,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if not 0.0 <= priority_alpha <= 1.0:
            raise ValueError("priority_alpha must be in [0, 1]")
        self.capacity = capacity
        self.priority_alpha = float(priority_alpha)
        self.observations = np.empty((capacity, observation_size), dtype=np.float32)
        self.next_observations = np.empty((capacity, observation_size), dtype=np.float32)
        self.actions = np.empty(capacity, dtype=np.int64)
        self.rewards = np.empty(capacity, dtype=np.float32)
        self.dones = np.empty(capacity, dtype=np.float32)
        self.discounts = np.empty(capacity, dtype=np.float32)
        self.tree_capacity = 1 << (capacity - 1).bit_length()
        self.priority_tree = np.zeros(2 * self.tree_capacity, dtype=np.float64)
        self.max_priority = 1.0
        self.position = 0
        self.size = 0

    def __len__(self) -> int:
        return self.size

    def _set_priority(self, index: int, priority: float) -> None:
        node = self.tree_capacity + index
        value = max(float(priority), 1e-6) ** self.priority_alpha
        difference = value - self.priority_tree[node]
        while node:
            self.priority_tree[node] += difference
            node //= 2

    def add(
        self,
        observation,
        action: int,
        reward: float,
        next_observation,
        done: bool,
        discount: float = 0.99,
    ) -> None:
        index = self.position
        self.observations[index] = observation
        self.actions[index] = action
        self.rewards[index] = reward
        self.next_observations[index] = next_observation
        self.dones[index] = float(done)
        self.discounts[index] = float(discount)
        self._set_priority(index, self.max_priority)
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

    def sample_prioritized(
        self,
        batch_size: int,
        device: torch.device,
        rng: np.random.Generator,
        beta: float,
    ):
        if self.size < batch_size:
            raise ValueError(f"need {batch_size} transitions, only {self.size} available")
        total = self.priority_tree[1]
        segment = total / batch_size
        masses = (np.arange(batch_size) + rng.random(batch_size)) * segment
        nodes = np.ones(batch_size, dtype=np.int64)
        while nodes[0] < self.tree_capacity:
            left = nodes * 2
            left_values = self.priority_tree[left]
            go_right = masses >= left_values
            masses = masses - left_values * go_right
            nodes = left + go_right
        indices = nodes - self.tree_capacity
        leaf_values = self.priority_tree[nodes]
        probabilities = leaf_values / total
        weights = (self.size * probabilities) ** (-beta)
        weights /= np.max(weights)
        return (
            torch.as_tensor(self.observations[indices], device=device),
            torch.as_tensor(self.actions[indices], device=device),
            torch.as_tensor(self.rewards[indices], device=device),
            torch.as_tensor(self.next_observations[indices], device=device),
            torch.as_tensor(self.dones[indices], device=device),
            torch.as_tensor(self.discounts[indices], device=device),
            torch.as_tensor(weights, dtype=torch.float32, device=device),
            indices,
        )

    def update_priorities(self, indices, priorities) -> None:
        for index, priority in zip(indices, priorities):
            raw_priority = max(float(priority), 1e-6)
            self.max_priority = max(self.max_priority, raw_priority)
            self._set_priority(int(index), raw_priority)


class AnchoredReplayBuffer:
    """Uniform online replay with a protected slice of champion demonstrations."""

    def __init__(
        self,
        capacity: int = 500_000,
        observation_size: int = 78,
        anchor_capacity: int = 50_000,
        anchor_fraction: float = 0.25,
        priority_alpha: float = 0.0,
    ) -> None:
        if not 0 < anchor_capacity < capacity:
            raise ValueError("anchor_capacity must be between zero and total capacity")
        if not 0.0 <= anchor_fraction < 1.0:
            raise ValueError("anchor_fraction must be in [0, 1)")
        self.online = ReplayBuffer(
            capacity - anchor_capacity, observation_size, priority_alpha
        )
        self.anchor = ReplayBuffer(anchor_capacity, observation_size, priority_alpha)
        self.anchor_fraction = float(anchor_fraction)

    def __len__(self) -> int:
        return len(self.online) + len(self.anchor)

    def add(
        self,
        observation,
        action: int,
        reward: float,
        next_observation,
        done: bool,
        discount: float = 0.99,
    ) -> None:
        self.online.add(observation, action, reward, next_observation, done, discount)

    def add_anchor(
        self,
        observation,
        action: int,
        reward: float,
        next_observation,
        done: bool,
        discount: float = 0.99,
    ) -> None:
        self.anchor.add(observation, action, reward, next_observation, done, discount)

    def sample(self, batch_size: int, device: torch.device, rng: np.random.Generator):
        if len(self) < batch_size:
            raise ValueError(f"need {batch_size} transitions, only {len(self)} available")

        anchor_count = min(int(round(batch_size * self.anchor_fraction)), len(self.anchor))
        online_count = batch_size - anchor_count
        if online_count > len(self.online):
            anchor_count += online_count - len(self.online)
            online_count = len(self.online)

        parts = []
        if online_count:
            parts.append(self.online.sample(online_count, device, rng))
        if anchor_count:
            parts.append(self.anchor.sample(anchor_count, device, rng))
        if len(parts) == 1:
            return parts[0]

        combined = tuple(torch.cat(values, dim=0) for values in zip(*parts))
        order = torch.as_tensor(rng.permutation(batch_size), device=device)
        return tuple(values[order] for values in combined)

    def sample_prioritized(
        self,
        batch_size: int,
        device: torch.device,
        rng: np.random.Generator,
        beta: float,
    ):
        if len(self) < batch_size:
            raise ValueError(f"need {batch_size} transitions, only {len(self)} available")
        anchor_count = min(int(round(batch_size * self.anchor_fraction)), len(self.anchor))
        online_count = batch_size - anchor_count
        if online_count > len(self.online):
            anchor_count += online_count - len(self.online)
            online_count = len(self.online)

        parts = []
        metadata = {"online_count": online_count, "online": None, "anchor": None}
        if online_count:
            sample = self.online.sample_prioritized(online_count, device, rng, beta)
            parts.append(sample[:7])
            metadata["online"] = sample[7]
        if anchor_count:
            sample = self.anchor.sample_prioritized(anchor_count, device, rng, beta)
            parts.append(sample[:7])
            metadata["anchor"] = sample[7]
        if len(parts) == 1:
            combined = parts[0]
        else:
            combined = tuple(torch.cat(values, dim=0) for values in zip(*parts))
        return (*combined, metadata)

    def update_priorities(self, metadata, priorities) -> None:
        online_count = metadata["online_count"]
        if metadata["online"] is not None:
            self.online.update_priorities(
                metadata["online"], priorities[:online_count]
            )
        if metadata["anchor"] is not None:
            self.anchor.update_priorities(
                metadata["anchor"], priorities[online_count:]
            )
