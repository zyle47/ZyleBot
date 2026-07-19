"""Q-network used by the from-scratch Breakout DQN."""

from __future__ import annotations

from torch import nn


class QNetwork(nn.Module):
    def __init__(self, observation_size: int = 78, action_count: int = 3) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(observation_size, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, action_count),
        )

    def forward(self, observation):
        return self.layers(observation)
