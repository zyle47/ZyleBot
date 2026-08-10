"""Train a layout-general Breakout policy with isolated SB3 rollouts."""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Callable

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import DQN, PPO
from stable_baselines3.common.callbacks import BaseCallback, CallbackList, CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecEnv
from stable_baselines3.common.utils import get_schedule_fn

import zl  # noqa: F401 - registers ZyleBreakout-v0
from zl.config import (
    EVAL_BUILTIN_SEEDS,
    HELD_OUT_LEVEL,
    TRAIN_BUILTIN_LEVELS,
    TRAIN_DEFAULTS,
    TRAIN_SEED,
    VALIDATION_PROCEDURAL_SEEDS,
)
from zl.env.mirror import MirrorAugmentation
from zl.policy import SemanticBreakoutCNN


def _training_builtins(args: argparse.Namespace) -> tuple[tuple[int, ...], int]:
    """Resolve which built-in levels train and which (if any) stays held out.

    ``--builtin-levels 1,2,3,4`` trains on the real game levels directly (mastery
    goal) and disables the hold-out; otherwise the generalization split holds out
    ``--held-out-level`` from the default TRAIN_BUILTIN_LEVELS.
    """
    if args.builtin_levels:
        return tuple(int(x) for x in str(args.builtin_levels).split(",")), -1
    builtin = tuple(level for level in TRAIN_BUILTIN_LEVELS if level != args.held_out_level)
    return builtin, args.held_out_level


def _training_weights(args: argparse.Namespace, training_levels: tuple[int, ...]) -> tuple[float, ...] | None:
    if not args.builtin_weights:
        return None
    weights = tuple(float(x) for x in str(args.builtin_weights).split(","))
    if len(weights) != len(training_levels):
        raise ValueError(
            f"--builtin-weights ({len(weights)}) must match the trained levels ({len(training_levels)})"
        )
    return weights


def _env_factory(rank: int, args: argparse.Namespace) -> Callable[[], gym.Env]:
    training_levels, held_out = _training_builtins(args)
    training_level_weights = _training_weights(args, training_levels)

    def make() -> gym.Env:
        env_kwargs = dict(
            level_mode=args.level_mode,
            level=args.fixed_level,
            held_out_level=held_out,
            training_levels=training_levels,
            training_level_weights=training_level_weights,
            procedural_probability=args.procedural_probability,
            procedural_large_probability=args.large_board_probability,
            max_levels=args.levels_per_episode,
            max_episode_steps=args.max_episode_steps,
            curriculum_clear_min=args.curriculum_clear_min,
            curriculum_clear_max=args.curriculum_clear_max,
            curriculum_probability=args.curriculum_probability,
            curriculum_bricks_min=args.curriculum_bricks_min,
            curriculum_bricks_max=args.curriculum_bricks_max,
            action_change_penalty=args.action_change_penalty,
            observation_version=args.observation_version,
        )
        env = gym.make("ZyleBreakout-v0", **env_kwargs)
        if args.mirror_probability > 0.0:
            # Training only: validation/eval stay unmirrored so numbers stay comparable.
            env = MirrorAugmentation(
                env, probability=args.mirror_probability, seed=args.seed + rank
            )
        env.reset(seed=args.seed + rank)
        return Monitor(env)

    return make


def _vector_env(args: argparse.Namespace) -> VecEnv:
    factories = [_env_factory(rank, args) for rank in range(args.n_envs)]
    if args.n_envs == 1:
        return DummyVecEnv(factories)
    return SubprocVecEnv(factories, start_method="spawn")


def _linear_schedule(initial: float, final_fraction: float = 0.10) -> Callable[[float], float]:
    """Anneal without reaching zero, so resumed runs can still adapt."""
    return lambda progress_remaining: initial * (
        final_fraction + (1.0 - final_fraction) * progress_remaining
    )


class ValidationCallback(BaseCallback):
    """Select checkpoints without looking at the sealed M3 test distribution."""

    def __init__(self, args: argparse.Namespace, eval_freq: int, save_dir: Path) -> None:
        super().__init__(verbose=1)
        self.args = args
        self.eval_freq = eval_freq
        self.save_dir = save_dir
        self.best_metric = float("-inf")

    def _run_episodes(self, env: gym.Env, seeds: list[int]) -> tuple[list[float], list[float], list[float]]:
        fractions: list[float] = []
        clears: list[float] = []
        scores: list[float] = []
        for seed in seeds:
            observation, info = env.reset(seed=seed)
            terminated = truncated = False
            while not (terminated or truncated):
                action, _ = self.model.predict(observation, deterministic=True)
                observation, _, terminated, truncated, info = env.step(
                    int(np.asarray(action).item())
                )
            fractions.append(float(info["bricks_cleared_fraction"]))
            clears.append(float(info["boards_cleared"] >= 1))
            scores.append(float(info["score"]))
        return fractions, clears, scores

    def _on_step(self) -> bool:
        if self.eval_freq <= 0 or self.n_calls % self.eval_freq:
            return True
        fractions: list[float] = []
        clears: list[float] = []
        scores: list[float] = []
        per_level: dict[int, float] = {}
        if self.args.validation_builtin:
            # Mastery selection: score each real game level on a plain full board
            # (no curriculum), so the saved best is the strongest all-round clearer.
            levels, _ = _training_builtins(self.args)
            cap = min(self.args.max_episode_steps, 8000)
            per = max(1, self.args.validation_episodes // len(levels))
            for lvl in levels:
                env = gym.make(
                    "ZyleBreakout-v0",
                    level_mode="fixed",
                    level=lvl,
                    max_levels=1,
                    max_episode_steps=cap,
                    observation_version=self.args.observation_version,
                )
                try:
                    f, c, s = self._run_episodes(env, [41_000 + lvl * 100 + k for k in range(per)])
                finally:
                    env.close()
                fractions += f
                clears += c
                scores += s
                per_level[lvl] = float(np.mean(c))
        else:
            if self.args.level_mode == "fixed":
                seeds = list(EVAL_BUILTIN_SEEDS[: self.args.validation_episodes])
                env_kwargs = dict(
                    level_mode="fixed",
                    level=self.args.fixed_level,
                    max_levels=1,
                    max_episode_steps=self.args.max_episode_steps,
                    observation_version=self.args.observation_version,
                )
            else:
                seeds = list(VALIDATION_PROCEDURAL_SEEDS[: self.args.validation_episodes])
                env_kwargs = dict(
                    level_mode="procedural",
                    max_levels=1,
                    max_episode_steps=self.args.max_episode_steps,
                    observation_version=self.args.observation_version,
                    procedural_large_probability=self.args.large_board_probability,
                )
            env = gym.make("ZyleBreakout-v0", **env_kwargs)
            try:
                fractions, clears, scores = self._run_episodes(env, seeds)
            finally:
                env.close()

        mean_fraction = float(np.mean(fractions))
        clear_rate = float(np.mean(clears))
        mean_score = float(np.mean(scores))
        # Clearing is the primary objective; partial progress breaks ties before
        # any game-score weighting can favor farming durable bricks.
        metric = 2.0 * clear_rate + mean_fraction
        self.logger.record("validation/clear_rate", clear_rate)
        self.logger.record("validation/bricks_cleared_fraction", mean_fraction)
        self.logger.record("validation/mean_score", mean_score)
        self.logger.record("validation/selection_metric", metric)
        for lvl, cr in per_level.items():
            self.logger.record(f"validation/clear_L{lvl}", cr)
        if self.verbose:
            extra = (
                "  " + " ".join(f"L{lvl}={cr:.2f}" for lvl, cr in per_level.items())
                if per_level
                else ""
            )
            print(
                f"validation step={self.num_timesteps} clear_rate={clear_rate:.3f} "
                f"bricks_fraction={mean_fraction:.3f} mean_score={mean_score:.1f}{extra}"
            )
        if metric > self.best_metric:
            self.best_metric = metric
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self.model.save(self.save_dir / "best_validation_model")
        return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=("ppo", "dqn"), default="ppo")
    parser.add_argument("--total-timesteps", type=int, default=TRAIN_DEFAULTS.total_timesteps)
    parser.add_argument("--n-envs", type=int, default=TRAIN_DEFAULTS.n_envs)
    parser.add_argument("--n-steps", type=int, default=TRAIN_DEFAULTS.n_steps)
    parser.add_argument("--batch-size", type=int, default=TRAIN_DEFAULTS.batch_size)
    parser.add_argument("--learning-rate", type=float, default=TRAIN_DEFAULTS.learning_rate)
    parser.add_argument("--ent-coef", type=float, default=TRAIN_DEFAULTS.ent_coef)
    parser.add_argument("--clip-range", type=float, default=TRAIN_DEFAULTS.clip_range)
    parser.add_argument("--target-kl", type=float, default=TRAIN_DEFAULTS.target_kl)
    parser.add_argument("--n-epochs", type=int, default=TRAIN_DEFAULTS.n_epochs)
    parser.add_argument("--seed", type=int, default=TRAIN_SEED)
    parser.add_argument("--level-mode", choices=("fixed", "mixed", "procedural"), default="mixed")
    parser.add_argument("--fixed-level", type=int, default=1)
    parser.add_argument("--held-out-level", type=int, default=HELD_OUT_LEVEL)
    parser.add_argument(
        "--builtin-levels",
        type=str,
        default=None,
        help="comma-separated built-in levels to train on directly, e.g. '1,2,3,4' to master "
        "the real game levels; overrides the hold-out split (nothing held out)",
    )
    parser.add_argument(
        "--validation-builtin",
        action="store_true",
        help="select checkpoints by mean clear-rate/bricks across the trained built-in levels "
        "(logs per-level validation/clear_L1..L4) instead of the procedural suite",
    )
    parser.add_argument(
        "--action-change-penalty",
        type=float,
        default=0.0,
        help="reward cost each time the action flips, to damp twitchy bang-bang paddle "
        "control (try 0.0005). Keep it TINY: episodes run thousands of decisions and "
        "~50%% of them currently flip, so 0.0005 already costs ~1.0 reward per episode "
        "against a ~4.0 full-clear reward. Training only; 0 = off",
    )
    parser.add_argument(
        "--mirror-probability",
        type=float,
        default=0.0,
        help="fraction of training episodes shown left-right mirrored (try 0.5) so the "
        "policy stops favoring one side and steers by where the bricks are",
    )
    parser.add_argument(
        "--builtin-weights",
        type=str,
        default=None,
        help="comma-separated sampling weights aligned with --builtin-levels, e.g. '1,1,3,4' to "
        "spend most gradient on the harder levels; default None samples them uniformly",
    )
    parser.add_argument(
        "--procedural-probability", type=float, default=TRAIN_DEFAULTS.procedural_probability
    )
    parser.add_argument(
        "--large-board-probability",
        type=float,
        default=None,
        help="override the procedural large-board rate (e.g. 0.5) to train on 1000+ brick "
        "level-4-scale boards; default None keeps the generator's per-family rate",
    )
    parser.add_argument("--levels-per-episode", type=int, default=3)
    parser.add_argument("--max-episode-steps", type=int, default=100_000)
    parser.add_argument("--curriculum-clear-min", type=float, default=0.0)
    parser.add_argument("--curriculum-clear-max", type=float, default=0.0)
    parser.add_argument("--curriculum-probability", type=float, default=1.0)
    parser.add_argument(
        "--curriculum-bricks-min",
        type=int,
        default=0,
        help="with --curriculum-bricks-max, start curriculum boards with this many bricks "
        "left; size-independent, so level 4 drills the same endgame as level 1",
    )
    parser.add_argument(
        "--curriculum-bricks-max",
        type=int,
        default=0,
        help="upper bound of bricks remaining (0 = use the fraction-based curriculum); "
        "takes precedence over --curriculum-clear-min/max when set",
    )
    parser.add_argument("--device", default="cuda", help="cuda, cpu, or auto")
    parser.add_argument("--run-dir", type=Path, default=Path("runs/generalization"))
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--validation-freq", type=int, default=TRAIN_DEFAULTS.checkpoint_freq)
    parser.add_argument("--validation-episodes", type=int, default=5)
    parser.add_argument(
        "--observation-version",
        choices=("v1", "v2"),
        default="v2",
        help="use v1 only when resuming a legacy 28-channel M1 checkpoint",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.n_envs < 1 or args.total_timesteps < 1:
        raise ValueError("n-envs and total-timesteps must be positive")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    run_dir = args.run_dir.resolve()
    checkpoint_dir = run_dir / "checkpoints"
    tensorboard_dir = run_dir / "tensorboard"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tensorboard_dir.mkdir(parents=True, exist_ok=True)
    vec_env = _vector_env(args)
    policy_kwargs = {
        "features_extractor_class": SemanticBreakoutCNN,
        "features_extractor_kwargs": {"features_dim": 512},
        "normalize_images": True,
    }

    algorithm_class = PPO if args.algorithm == "ppo" else DQN
    learning_rate = _linear_schedule(args.learning_rate)
    if args.resume:
        model = algorithm_class.load(args.resume, env=vec_env, device=args.device)
        # Checkpoints retain their original TensorBoard directory. Redirect resumed
        # experiments so evidence from a changed curriculum is not mixed with M1.
        model.tensorboard_log = str(tensorboard_dir)
        model.learning_rate = learning_rate
        model.lr_schedule = learning_rate
        if args.algorithm == "ppo":
            model.clip_range = get_schedule_fn(args.clip_range)
            model.target_kl = args.target_kl
            model.n_epochs = args.n_epochs
            model.ent_coef = args.ent_coef
    elif args.algorithm == "ppo":
        model = PPO(
            "CnnPolicy",
            vec_env,
            learning_rate=learning_rate,
            n_steps=args.n_steps,
            batch_size=args.batch_size,
            gamma=TRAIN_DEFAULTS.gamma,
            gae_lambda=TRAIN_DEFAULTS.gae_lambda,
            ent_coef=args.ent_coef,
            clip_range=args.clip_range,
            target_kl=args.target_kl,
            n_epochs=args.n_epochs,
            policy_kwargs=policy_kwargs,
            tensorboard_log=str(tensorboard_dir),
            seed=args.seed,
            device=args.device,
            verbose=1,
        )
    else:
        model = DQN(
            "CnnPolicy",
            vec_env,
            learning_rate=_linear_schedule(1.0e-4),
            buffer_size=200_000,
            learning_starts=20_000,
            batch_size=args.batch_size,
            gamma=TRAIN_DEFAULTS.gamma,
            train_freq=4,
            gradient_steps=1,
            target_update_interval=10_000,
            exploration_fraction=0.15,
            policy_kwargs=policy_kwargs,
            tensorboard_log=str(tensorboard_dir),
            seed=args.seed,
            device=args.device,
            verbose=1,
        )

    save_freq = max(1, TRAIN_DEFAULTS.checkpoint_freq // args.n_envs)
    checkpoints = CheckpointCallback(
        save_freq=save_freq,
        save_path=str(checkpoint_dir),
        name_prefix=f"zyle_{args.algorithm}",
    )
    callbacks: BaseCallback = checkpoints
    if args.validation_freq > 0:
        validation = ValidationCallback(
            args,
            eval_freq=max(1, args.validation_freq // args.n_envs),
            save_dir=checkpoint_dir,
        )
        callbacks = CallbackList([checkpoints, validation])
    try:
        model.learn(total_timesteps=args.total_timesteps, callback=callbacks)
        final_path = checkpoint_dir / f"zyle_{args.algorithm}_final"
        model.save(final_path)
        training_levels, held_out = _training_builtins(args)
        print(f"saved={final_path}.zip")
        print(f"tensorboard={tensorboard_dir}")
        print(
            f"training_builtins={training_levels} "
            f"held_out_level={held_out if held_out >= 0 else 'none'}"
        )
    finally:
        vec_env.close()


if __name__ == "__main__":
    main()
