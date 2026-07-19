"""Evaluate a trained checkpoint greedily."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from rl.breakout_env import BreakoutEnv
from rl.dqn.agent import DQNAgent
from rl.train import choose_device


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=Path, required=True)
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()

    device = choose_device(args.device)
    checkpoint = torch.load(args.ckpt, map_location=device, weights_only=False)
    agent = DQNAgent(device, seed=args.seed)
    agent.load_checkpoint(checkpoint)
    scores, lengths = [], []
    for episode in range(args.episodes):
        env = BreakoutEnv()
        observation, _ = env.reset(seed=args.seed + episode)
        done = False
        steps = 0
        info = {"score": 0}
        while not done:
            observation, _, terminated, truncated, info = env.step(
                agent.act(observation, greedy=True)
            )
            done = terminated or truncated
            steps += 1
        scores.append(info["score"])
        lengths.append(steps)
        print(f"episode {episode + 1}: score={info['score']} steps={steps}")
    print(f"mean: score={np.mean(scores):.1f} steps={np.mean(lengths):.1f}")


if __name__ == "__main__":
    main()
