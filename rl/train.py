"""Train the hand-built Double-DQN agent on level-one Breakout."""

from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from tqdm import tqdm

from rl.breakout_env import BreakoutEnv
from rl.dqn.agent import BATCH_SIZE, LEARNING_RATE, DQNAgent
from rl.dqn.replay_buffer import ReplayBuffer

WARMUP_STEPS = 10_000
EVAL_INTERVAL = 10_000
EVAL_EPISODES = 5


def choose_device(name: str) -> torch.device:
    if name == "auto":
        name = "cuda" if torch.cuda.is_available() else "cpu"
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
    return torch.device(name)


def evaluate(agent: DQNAgent, seed: int, episodes: int = EVAL_EPISODES) -> tuple[float, float]:
    scores, lengths = [], []
    for episode in range(episodes):
        env = BreakoutEnv()
        observation, _ = env.reset(seed=seed + 100_000 + episode)
        done = False
        steps = 0
        info = {"score": 0}
        while not done:
            observation, _, terminated, truncated, info = env.step(
                agent.act(observation, greedy=True)
            )
            done = terminated or truncated
            steps += 1
        scores.append(float(info["score"]))
        lengths.append(float(steps))
    return float(np.mean(scores)), float(np.mean(lengths))


def save_checkpoint(
    path: Path,
    agent: DQNAgent,
    best_eval_score: float,
    run_dir: Path,
    eval_episodes: int,
) -> None:
    payload = agent.checkpoint(best_eval_score=best_eval_score)
    payload["run_dir"] = str(run_dir)
    payload["eval_episodes"] = eval_episodes
    torch.save(payload, path)


def create_run_dir(root: Path = Path("rl/runs"), timestamp: str | None = None) -> Path:
    """Return a new timestamped path, adding a suffix on same-second collisions."""
    stem = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = root / stem
    suffix = 2
    while candidate.exists():
        candidate = root / f"{stem}-{suffix}"
        suffix += 1
    return candidate


def publish_best(agent: DQNAgent, eval_score: float) -> None:
    """Publish lazily so ordinary training startup does not import export code."""
    from rl.export_policy import arrays_from_online_state, publish_policy

    publish_policy(
        arrays_from_online_state(agent.online.state_dict()),
        training_steps=agent.agent_steps,
        eval_score=eval_score,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=2_000_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--resume", type=Path)
    parser.add_argument(
        "--learning-rate",
        type=float,
        help="Adam rate; fresh runs default to 1e-4, resumes preserve the checkpoint rate",
    )
    parser.add_argument(
        "--eval-episodes",
        type=int,
        help="greedy episodes per evaluation; fresh runs default to 5",
    )
    parser.add_argument(
        "--fork-run",
        action="store_true",
        help="resume into a new run directory and re-baseline its best score",
    )
    parser.add_argument(
        "--live-export",
        action="store_true",
        help="Also atomically publish each new best checkpoint to rl/policy/ for "
        "the app to hot-reload. Off by default; never publishes on non-improving evals.",
    )
    args = parser.parse_args()
    if args.steps <= 0:
        parser.error("--steps must be positive")
    if args.learning_rate is not None and (
        not math.isfinite(args.learning_rate) or args.learning_rate <= 0
    ):
        parser.error("--learning-rate must be a positive finite number")
    if args.eval_episodes is not None and args.eval_episodes <= 0:
        parser.error("--eval-episodes must be positive")
    if args.fork_run and not args.resume:
        parser.error("--fork-run requires --resume")

    device = choose_device(args.device)
    agent = DQNAgent(
        device,
        seed=args.seed,
        learning_rate=args.learning_rate or LEARNING_RATE,
    )
    start_step = 0
    best_eval_score = float("-inf")
    eval_episodes = args.eval_episodes or EVAL_EPISODES
    if args.resume:
        checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
        agent.load_checkpoint(checkpoint)
        checkpoint_eval_episodes = int(checkpoint.get("eval_episodes", EVAL_EPISODES))
        if args.eval_episodes is None:
            eval_episodes = checkpoint_eval_episodes
        elif args.eval_episodes != checkpoint_eval_episodes and not args.fork_run:
            parser.error(
                "changing --eval-episodes on a resumed log requires --fork-run"
            )
        if args.learning_rate is not None:
            agent.set_learning_rate(args.learning_rate)
        start_step = agent.agent_steps
        best_eval_score = float(checkpoint.get("best_eval_score", float("-inf")))
        run_dir = (
            create_run_dir()
            if args.fork_run
            else Path(checkpoint.get("run_dir", args.resume.parent))
        )
    else:
        run_dir = create_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "log.csv"
    if not log_path.exists():
        with log_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                ("steps", "epsilon", "loss", "train_return", "eval_score_mean", "eval_len_mean")
            )

    print(
        f"Run: {run_dir} | start={start_step} | add={args.steps} | "
        f"lr={agent.learning_rate:g} | eval_episodes={eval_episodes} | "
        f"fork={args.fork_run} | live_export={args.live_export}"
    )

    if args.fork_run:
        # A fork may change evaluation count, so measure a fresh, comparable
        # baseline and keep source weights as both local latest.pt and best.pt.
        baseline_score, baseline_length = evaluate(
            agent, args.seed + agent.agent_steps, eval_episodes
        )
        best_eval_score = baseline_score
        with log_path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                (
                    agent.agent_steps,
                    agent.epsilon,
                    float("nan"),
                    float("nan"),
                    baseline_score,
                    baseline_length,
                )
            )
        save_checkpoint(
            run_dir / "latest.pt", agent, best_eval_score, run_dir, eval_episodes
        )
        save_checkpoint(
            run_dir / "best.pt", agent, best_eval_score, run_dir, eval_episodes
        )
        if args.live_export:
            publish_best(agent, best_eval_score)
        print(f"Fork baseline: score={baseline_score:.1f}, length={baseline_length:.1f}")

    env = BreakoutEnv()
    observation, _ = env.reset(seed=args.seed)
    replay = ReplayBuffer()
    episode_return = 0.0
    recent_returns: list[float] = []
    losses: list[float] = []
    rng = np.random.default_rng(args.seed + 1)
    target_step = start_step + args.steps
    # Normal runs use the pinned 10k warmup. A deliberately short smoke run
    # gets enough post-warmup updates to verify loss/checkpoint plumbing.
    warmup = WARMUP_STEPS if args.steps > WARMUP_STEPS else max(BATCH_SIZE, args.steps // 5)

    progress = tqdm(total=args.steps, desc=f"DQN ({device.type})")
    while agent.agent_steps < target_step:
        if agent.agent_steps - start_step < warmup:
            action = int(rng.integers(0, 3))
            agent.agent_steps += 1
        else:
            action = agent.act(observation)
        next_observation, reward, terminated, truncated, _ = env.step(action)
        done = terminated or truncated
        replay.add(observation, action, reward, next_observation, done)
        observation = next_observation
        episode_return += reward

        if agent.agent_steps - start_step >= warmup and len(replay) >= BATCH_SIZE:
            losses.append(agent.learn(replay))
        if done:
            recent_returns.append(episode_return)
            recent_returns = recent_returns[-100:]
            episode_return = 0.0
            observation, _ = env.reset(seed=args.seed + agent.agent_steps)

        relative_step = agent.agent_steps - start_step
        should_evaluate = agent.agent_steps % EVAL_INTERVAL == 0 or relative_step == args.steps
        if should_evaluate:
            eval_score, eval_length = evaluate(
                agent, args.seed + agent.agent_steps, eval_episodes
            )
            mean_loss = float(np.mean(losses[-EVAL_INTERVAL:])) if losses else float("nan")
            mean_return = float(np.mean(recent_returns)) if recent_returns else episode_return
            with log_path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    (agent.agent_steps, agent.epsilon, mean_loss, mean_return, eval_score, eval_length)
                )
            is_new_best = eval_score > best_eval_score
            if is_new_best:
                best_eval_score = eval_score
            save_checkpoint(
                run_dir / "latest.pt", agent, best_eval_score, run_dir, eval_episodes
            )
            if is_new_best:
                save_checkpoint(
                    run_dir / "best.pt", agent, best_eval_score, run_dir, eval_episodes
                )
                if args.live_export:
                    # Publish only improved bests (never latest.pt), atomically,
                    # so the running app hot-reloads without a restart. Checkpoint
                    # selection above is unchanged.
                    publish_best(agent, best_eval_score)
            progress.set_postfix(loss=f"{mean_loss:.4f}", eval=f"{eval_score:.0f}")
        progress.update(1)
    progress.close()
    print(f"Run saved to {run_dir}")


if __name__ == "__main__":
    main()
