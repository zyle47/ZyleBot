"""Neural feature extractors shared by training and checkpoint loading."""

from __future__ import annotations

import gymnasium as gym
import torch
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from torch import nn


class SemanticBreakoutCNN(BaseFeaturesExtractor):
    """Small-stride CNN that preserves the two-pixel ball better than NatureCNN."""

    def __init__(self, observation_space: gym.spaces.Box, features_dim: int = 512) -> None:
        super().__init__(observation_space, features_dim)
        channels = int(observation_space.shape[0])
        self.cnn = nn.Sequential(
            nn.Conv2d(channels, 32, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((6, 6)),
            nn.Flatten(),
            nn.Linear(128 * 6 * 6, features_dim),
            nn.ReLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        return self.cnn(observations)

