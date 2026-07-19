"""Epsilon-greedy Double-DQN agent, built without an RL framework."""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from rl.dqn.network import QNetwork

EPSILON_START = 1.0
EPSILON_END = 0.05
EPSILON_DECAY_STEPS = 150_000
GAMMA = 0.99
BATCH_SIZE = 256
LEARNING_RATE = 1e-4
TARGET_SYNC_INTERVAL = 2_000


class DQNAgent:
    def __init__(self, device: torch.device, seed: int = 0) -> None:
        self.device = device
        torch.manual_seed(seed)
        if device.type == "cuda":
            torch.cuda.manual_seed_all(seed)
        self.online = QNetwork().to(device)
        # A lagged target keeps bootstrapping targets stable while the online
        # network changes on every gradient step.
        self.target = QNetwork().to(device)
        self.target.load_state_dict(self.online.state_dict())
        self.target.eval()
        self.optimizer = torch.optim.Adam(self.online.parameters(), lr=LEARNING_RATE)
        self.loss_fn = nn.SmoothL1Loss()
        self.agent_steps = 0
        self.learn_steps = 0
        self.rng = np.random.default_rng(seed)

    @property
    def epsilon(self) -> float:
        fraction = min(1.0, self.agent_steps / EPSILON_DECAY_STEPS)
        return EPSILON_START + fraction * (EPSILON_END - EPSILON_START)

    def act(self, observation: np.ndarray, *, greedy: bool = False) -> int:
        if not greedy and self.rng.random() < self.epsilon:
            action = int(self.rng.integers(0, 3))
        else:
            with torch.no_grad():
                tensor = torch.as_tensor(observation, device=self.device).unsqueeze(0)
                action = int(self.online(tensor).argmax(dim=1).item())
        if not greedy:
            self.agent_steps += 1
        return action

    def learn(self, replay) -> float:
        observations, actions, rewards, next_observations, dones = replay.sample(
            BATCH_SIZE, self.device, self.rng
        )
        chosen_q = self.online(observations).gather(1, actions.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            # Double DQN separates action selection (online) from evaluation
            # (target), reducing the maximization-driven Q overestimation bias.
            next_actions = self.online(next_observations).argmax(dim=1, keepdim=True)
            next_q = self.target(next_observations).gather(1, next_actions).squeeze(1)
            targets = rewards + GAMMA * (1.0 - dones) * next_q

        loss = self.loss_fn(chosen_q, targets)
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        self.optimizer.step()
        self.learn_steps += 1
        if self.learn_steps % TARGET_SYNC_INTERVAL == 0:
            self.target.load_state_dict(self.online.state_dict())
        return float(loss.item())

    def checkpoint(self, *, best_eval_score: float) -> dict:
        return {
            "online": self.online.state_dict(),
            "target": self.target.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "agent_steps": self.agent_steps,
            "learn_steps": self.learn_steps,
            "epsilon": self.epsilon,
            "best_eval_score": best_eval_score,
        }

    def load_checkpoint(self, checkpoint: dict) -> None:
        self.online.load_state_dict(checkpoint["online"])
        self.target.load_state_dict(checkpoint.get("target", checkpoint["online"]))
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        self.agent_steps = int(checkpoint["agent_steps"])
        self.learn_steps = int(checkpoint.get("learn_steps", 0))
