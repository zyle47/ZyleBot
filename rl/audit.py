"""Standalone paired A/B audit between two DQN checkpoints.

Reuses the trainer's evaluation and gating so a promotion decision matches exactly what
the training loop would do: both policies are evaluated greedily on identical per-episode
seeds (common random numbers), then compared with the same paired 95%-confidence gate.

Use it before deploying an experimental policy over the live champion, e.g.::

    rl\\venv\\Scripts\\python.exe -m rl.audit ^
        --candidate rl\\runs\\<new-run>\\best.pt ^
        --incumbent rl\\runs\\20260720-125627\\best.pt ^
        --episodes 200

A "PROMOTE" verdict means the candidate's paired mean-difference lower-95 bound cleared the
same +10 bar the trainer uses; only then publish via ``rl.export_policy``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from rl.dqn.agent import DQNAgent
from rl.train import (
    CHAMPION_MIN_IMPROVEMENT,
    choose_device,
    evaluate_episodes,
    paired_gate_decision,
)


def load_agent(path: Path, device: torch.device) -> DQNAgent:
    """Load a checkpoint into a fresh agent for greedy evaluation."""
    agent = DQNAgent(device)
    checkpoint = torch.load(path, map_location=device, weights_only=False)
    agent.load_checkpoint(checkpoint)
    return agent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, help="checkpoint under test")
    parser.add_argument(
        "--incumbent", type=Path, required=True, help="deployed champion checkpoint"
    )
    parser.add_argument(
        "--episodes", type=int, default=200, help="paired greedy episodes (default 200)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="shared evaluation seed (default 42)"
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=CHAMPION_MIN_IMPROVEMENT,
        help="lower-95 bound the candidate mean-difference must exceed to pass (default 10)",
    )
    args = parser.parse_args()
    if args.episodes < 2:
        parser.error("--episodes must be at least 2")

    device = choose_device(args.device)
    candidate = load_agent(args.candidate, device)
    incumbent = load_agent(args.incumbent, device)

    # Identical seed -> identical per-episode env seeds inside evaluate_episodes -> a valid
    # paired (common-random-numbers) comparison, exactly as the trainer's champion gate.
    candidate_scores, candidate_lengths = evaluate_episodes(candidate, args.seed, args.episodes)
    incumbent_scores, incumbent_lengths = evaluate_episodes(incumbent, args.seed, args.episodes)

    gate = paired_gate_decision(candidate_scores, incumbent_scores, args.min_improvement)
    verdict = "PROMOTE" if gate["accepted"] else "KEEP INCUMBENT"
    print(f"Episodes (paired): {args.episodes} | seed {args.seed} | device {device.type}")
    print(
        f"Candidate : mean {gate['candidate_mean']:.2f} "
        f"(len {float(np.mean(candidate_lengths)):.1f})  [{args.candidate}]"
    )
    print(
        f"Incumbent : mean {gate['incumbent_mean']:.2f} "
        f"(len {float(np.mean(incumbent_lengths)):.1f})  [{args.incumbent}]"
    )
    print(
        f"Difference: {gate['mean_difference']:+.2f} "
        f"(lower-95 {gate['lower_confidence']:+.2f}, bar >{args.min_improvement:g})"
    )
    print(f"Verdict   : {verdict}")


if __name__ == "__main__":
    main()
