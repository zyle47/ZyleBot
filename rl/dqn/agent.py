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
GRADIENT_CLIP_NORM = 10.0
PER_BETA_START = 0.4
PER_BETA_STEPS = 1_000_000
PRIORITY_EPSILON = 1e-3


class DQNAgent:
    def __init__(
        self,
        device: torch.device,
        seed: int = 0,
        learning_rate: float = LEARNING_RATE,
        gradient_clip_norm: float | None = GRADIENT_CLIP_NORM,
        gamma: float = GAMMA,
        epsilon_decay_steps: int = EPSILON_DECAY_STEPS,
    ) -> None:
        if not np.isfinite(gamma) or not 0.0 < gamma < 1.0:
            raise ValueError("gamma must be in (0, 1)")
        if epsilon_decay_steps <= 0:
            raise ValueError("epsilon_decay_steps must be positive")
        self.device = device
        self.gamma = float(gamma)
        self.epsilon_decay_steps = int(epsilon_decay_steps)
        torch.manual_seed(seed)
        if device.type == "cuda":
            torch.cuda.manual_seed_all(seed)
        self.online = QNetwork().to(device)
        # A lagged target keeps bootstrapping targets stable while the online
        # network changes on every gradient step.
        self.target = QNetwork().to(device)
        self.target.load_state_dict(self.online.state_dict())
        self.target.eval()
        self.optimizer = torch.optim.Adam(self.online.parameters(), lr=learning_rate)
        self.loss_fn = nn.SmoothL1Loss(reduction="none")
        self.set_gradient_clip_norm(gradient_clip_norm)
        self.agent_steps = 0
        self.learn_steps = 0
        self.priority_steps = 0
        self.exploration_boost_start_step = 0
        self.exploration_boost_end_step = 0
        self.exploration_boost_peak = EPSILON_END
        self.rng = np.random.default_rng(seed)

    @property
    def epsilon(self) -> float:
        fraction = min(1.0, self.agent_steps / self.epsilon_decay_steps)
        base_epsilon = EPSILON_START + fraction * (EPSILON_END - EPSILON_START)
        if self.agent_steps >= self.exploration_boost_end_step:
            return base_epsilon
        duration = self.exploration_boost_end_step - self.exploration_boost_start_step
        if duration <= 0:
            return base_epsilon
        boost_fraction = max(
            0.0,
            min(1.0, (self.agent_steps - self.exploration_boost_start_step) / duration),
        )
        boosted_epsilon = self.exploration_boost_peak + boost_fraction * (
            EPSILON_END - self.exploration_boost_peak
        )
        return max(base_epsilon, boosted_epsilon)

    @property
    def learning_rate(self) -> float:
        return float(self.optimizer.param_groups[0]["lr"])

    def set_learning_rate(self, learning_rate: float) -> None:
        """Override Adam's rate while preserving its learned moment estimates."""
        for param_group in self.optimizer.param_groups:
            param_group["lr"] = learning_rate

    def reset_optimizer(self) -> None:
        """Start fresh Adam moments while keeping the configured learning rate."""
        self.optimizer = torch.optim.Adam(
            self.online.parameters(),
            lr=self.learning_rate,
        )
        self.priority_steps = 0

    def set_gradient_clip_norm(self, gradient_clip_norm: float | None) -> None:
        """Cap global gradient norm, or disable clipping with ``None``."""
        if gradient_clip_norm is not None and (
            not np.isfinite(gradient_clip_norm) or gradient_clip_norm <= 0
        ):
            raise ValueError("gradient_clip_norm must be positive and finite, or None")
        self.gradient_clip_norm = gradient_clip_norm

    def start_exploration_boost(self, *, peak: float, duration_steps: int) -> None:
        """Temporarily raise epsilon, then linearly cool it back to the normal schedule."""
        if not np.isfinite(peak) or not EPSILON_END < peak <= 1.0:
            raise ValueError(f"peak must be in ({EPSILON_END}, 1]")
        if duration_steps <= 0:
            raise ValueError("duration_steps must be positive")
        self.exploration_boost_start_step = self.agent_steps
        self.exploration_boost_end_step = self.agent_steps + duration_steps
        self.exploration_boost_peak = float(peak)

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
        priority_metadata = None
        if hasattr(replay, "sample_prioritized"):
            beta_fraction = min(1.0, self.priority_steps / PER_BETA_STEPS)
            beta = PER_BETA_START + beta_fraction * (1.0 - PER_BETA_START)
            (
                observations,
                actions,
                rewards,
                next_observations,
                dones,
                discounts,
                importance_weights,
                priority_metadata,
            ) = replay.sample_prioritized(BATCH_SIZE, self.device, self.rng, beta)
        else:
            observations, actions, rewards, next_observations, dones = replay.sample(
                BATCH_SIZE, self.device, self.rng
            )
            discounts = torch.full_like(rewards, self.gamma)
            importance_weights = torch.ones_like(rewards)
        chosen_q = self.online(observations).gather(1, actions.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            # Double DQN separates action selection (online) from evaluation
            # (target), reducing the maximization-driven Q overestimation bias.
            next_actions = self.online(next_observations).argmax(dim=1, keepdim=True)
            next_q = self.target(next_observations).gather(1, next_actions).squeeze(1)
            targets = rewards + discounts * (1.0 - dones) * next_q

        td_errors = targets - chosen_q
        loss = (self.loss_fn(chosen_q, targets) * importance_weights).mean()
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        if self.gradient_clip_norm is not None:
            torch.nn.utils.clip_grad_norm_(self.online.parameters(), self.gradient_clip_norm)
        self.optimizer.step()
        if priority_metadata is not None:
            priorities = (
                td_errors.detach().abs().cpu().numpy() + PRIORITY_EPSILON
            )
            replay.update_priorities(priority_metadata, priorities)
            self.priority_steps += 1
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
            "priority_steps": self.priority_steps,
            "epsilon": self.epsilon,
            "learning_rate": self.learning_rate,
            "gradient_clip_norm": self.gradient_clip_norm,
            "gamma": self.gamma,
            "epsilon_decay_steps": self.epsilon_decay_steps,
            "exploration_boost_start_step": self.exploration_boost_start_step,
            "exploration_boost_end_step": self.exploration_boost_end_step,
            "exploration_boost_peak": self.exploration_boost_peak,
            "best_eval_score": best_eval_score,
        }

    def load_checkpoint(self, checkpoint: dict) -> None:
        self.online.load_state_dict(checkpoint["online"])
        self.target.load_state_dict(checkpoint.get("target", checkpoint["online"]))
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        # Checkpoints written before clipping existed represent the legacy,
        # unclipped optimiser and should resume reproducibly by default.
        self.gradient_clip_norm = checkpoint.get("gradient_clip_norm")
        # Legacy checkpoints predate configurable discount/exploration; fall back to the
        # module defaults they were trained under so a plain resume is reproducible.
        self.gamma = float(checkpoint.get("gamma", GAMMA))
        self.epsilon_decay_steps = int(
            checkpoint.get("epsilon_decay_steps", EPSILON_DECAY_STEPS)
        )
        self.agent_steps = int(checkpoint["agent_steps"])
        self.learn_steps = int(checkpoint.get("learn_steps", 0))
        self.priority_steps = int(checkpoint.get("priority_steps", 0))
        self.exploration_boost_start_step = int(
            checkpoint.get("exploration_boost_start_step", 0)
        )
        self.exploration_boost_end_step = int(
            checkpoint.get("exploration_boost_end_step", 0)
        )
        self.exploration_boost_peak = float(
            checkpoint.get("exploration_boost_peak", EPSILON_END)
        )
