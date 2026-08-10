"""Evaluate zero-shot Breakout generalization on fixed, never-trained seeds."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Protocol

import gymnasium as gym
import numpy as np
from stable_baselines3 import DQN, PPO

import zl  # noqa: F401 - registers ZyleBreakout-v0
from zl.config import EVAL_BUILTIN_SEEDS, EVAL_PROCEDURAL_SEEDS, HELD_OUT_LEVEL


class Policy(Protocol):
    def predict(self, observation: np.ndarray, deterministic: bool = True) -> tuple[Any, Any]: ...


@dataclass(frozen=True)
class EpisodeResult:
    suite: str
    seed: int
    score: int
    cleared: bool
    bricks_cleared_fraction: float
    steps: int
    level_name: str
    level_family: str


class BaselinePolicy:
    def __init__(self, kind: str, seed: int) -> None:
        self.kind = kind
        self.rng = np.random.default_rng(seed)

    def predict(self, observation: np.ndarray, deterministic: bool = True) -> tuple[int, None]:
        del observation, deterministic
        return (0 if self.kind == "noop" else int(self.rng.integers(3))), None


def _episode(env: gym.Env, policy: Policy, seed: int, suite: str) -> EpisodeResult:
    observation, info = env.reset(seed=seed)
    terminated = truncated = False
    steps = 0
    while not (terminated or truncated):
        action, _ = policy.predict(observation, deterministic=True)
        observation, _, terminated, truncated, info = env.step(int(np.asarray(action).item()))
        steps += 1
    return EpisodeResult(
        suite=suite,
        seed=seed,
        score=int(info["score"]),
        cleared=bool(info["boards_cleared"] >= 1),
        bricks_cleared_fraction=float(info["bricks_cleared_fraction"]),
        steps=steps,
        level_name=str(info["level_name"]),
        level_family=str(info["level_family"]),
    )


def _summary(results: list[EpisodeResult]) -> dict[str, float | int | str]:
    return {
        "suite": results[0].suite,
        "episodes": len(results),
        "mean_score": fmean(item.score for item in results),
        "clear_rate": fmean(float(item.cleared) for item in results),
        "mean_bricks_cleared_fraction": fmean(item.bricks_cleared_fraction for item in results),
        "mean_steps": fmean(item.steps for item in results),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", type=Path, default=Path("runs/generalization/checkpoints/zyle_ppo_final.zip")
    )
    parser.add_argument("--algorithm", choices=("ppo", "dqn"), default="ppo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--held-out", action="store_true", help="run only held-out built-in suite")
    parser.add_argument("--procedural-only", action="store_true")
    parser.add_argument("--held-out-level", type=int, default=HELD_OUT_LEVEL)
    parser.add_argument("--procedural-count", type=int, default=len(EVAL_PROCEDURAL_SEEDS))
    parser.add_argument("--max-episode-steps", type=int, default=100_000)
    parser.add_argument("--baseline", choices=("random", "noop"))
    parser.add_argument(
        "--observation-version",
        choices=("auto", "v1", "v2"),
        default="auto",
        help="auto detects legacy model channels; baselines default to v2",
    )
    parser.add_argument("--json", type=Path, help="optional per-episode JSON output")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.held_out and args.procedural_only:
        raise ValueError("--held-out and --procedural-only are mutually exclusive")
    if args.baseline:
        policy: Policy = BaselinePolicy(args.baseline, seed=73_001)
        label = f"baseline:{args.baseline}"
    else:
        cls = PPO if args.algorithm == "ppo" else DQN
        policy = cls.load(args.model, device=args.device)
        label = str(args.model.resolve())

    observation_version = args.observation_version
    if observation_version == "auto":
        if args.baseline:
            observation_version = "v2"
        else:
            channels = int(policy.observation_space.shape[0])
            if channels == 28:
                observation_version = "v1"
            elif channels == 32:
                observation_version = "v2"
            else:
                raise ValueError(f"cannot infer observation version from {channels} channels")

    suites: list[tuple[str, gym.Env, tuple[int, ...]]] = []
    if not args.procedural_only:
        suites.append(
            (
                f"held-out-built-in-{args.held_out_level}",
                gym.make(
                    "ZyleBreakout-v0",
                    level_mode="fixed",
                    level=args.held_out_level,
                    max_levels=1,
                    max_episode_steps=args.max_episode_steps,
                    observation_version=observation_version,
                ),
                EVAL_BUILTIN_SEEDS,
            )
        )
    if not args.held_out:
        seeds = EVAL_PROCEDURAL_SEEDS[: args.procedural_count]
        suites.append(
            (
                "fresh-procedural",
                gym.make(
                    "ZyleBreakout-v0",
                    level_mode="procedural",
                    max_levels=1,
                    max_episode_steps=args.max_episode_steps,
                    observation_version=observation_version,
                ),
                seeds,
            )
        )

    all_results: list[EpisodeResult] = []
    print(f"policy={label}")
    try:
        for name, env, seeds in suites:
            results = [_episode(env, policy, seed, name) for seed in seeds]
            all_results.extend(results)
            summary = _summary(results)
            print(
                f"suite={name} episodes={summary['episodes']} "
                f"mean_score={summary['mean_score']:.2f} "
                f"clear_rate={summary['clear_rate']:.3f} "
                f"bricks_cleared_fraction={summary['mean_bricks_cleared_fraction']:.3f} "
                f"mean_steps={summary['mean_steps']:.1f}"
            )
    finally:
        for _, env, _ in suites:
            env.close()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "policy": label,
            "summaries": [_summary([item for item in all_results if item.suite == name]) for name, _, _ in suites],
            "episodes": [asdict(item) for item in all_results],
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"json={args.json.resolve()}")


if __name__ == "__main__":
    main()
