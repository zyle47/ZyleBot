"""Train the hand-built Double-DQN agent on level-one Breakout."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from tqdm import tqdm

from rl.breakout_env import BreakoutEnv
from rl.dqn.agent import BATCH_SIZE, DQNAgent
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


def evaluate(agent: DQNAgent, seed: int) -> tuple[float, float]:
    scores, lengths = [], []
    for episode in range(EVAL_EPISODES):
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


def save_checkpoint(path: Path, agent: DQNAgent, best_eval_score: float, run_dir: Path) -> None:
    payload = agent.checkpoint(best_eval_score=best_eval_score)
    payload["run_dir"] = str(run_dir)
    torch.save(payload, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=2_000_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    if args.steps <= 0:
        parser.error("--steps must be positive")

    device = choose_device(args.device)
    agent = DQNAgent(device, seed=args.seed)
    start_step = 0
    best_eval_score = float("-inf")
    if args.resume:
        checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
        agent.load_checkpoint(checkpoint)
        start_step = agent.agent_steps
        best_eval_score = float(checkpoint.get("best_eval_score", float("-inf")))
        run_dir = Path(checkpoint.get("run_dir", args.resume.parent))
    else:
        run_dir = Path("rl/runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "log.csv"
    if not log_path.exists():
        with log_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                ("steps", "epsilon", "loss", "train_return", "eval_score_mean", "eval_len_mean")
            )

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
            eval_score, eval_length = evaluate(agent, args.seed + agent.agent_steps)
            mean_loss = float(np.mean(losses[-EVAL_INTERVAL:])) if losses else float("nan")
            mean_return = float(np.mean(recent_returns)) if recent_returns else episode_return
            with log_path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    (agent.agent_steps, agent.epsilon, mean_loss, mean_return, eval_score, eval_length)
                )
            is_new_best = eval_score > best_eval_score
            if is_new_best:
                best_eval_score = eval_score
            save_checkpoint(run_dir / "latest.pt", agent, best_eval_score, run_dir)
            if is_new_best:
                save_checkpoint(run_dir / "best.pt", agent, best_eval_score, run_dir)
            progress.set_postfix(loss=f"{mean_loss:.4f}", eval=f"{eval_score:.0f}")
        progress.update(1)
    progress.close()
    print(f"Run saved to {run_dir}")


if __name__ == "__main__":
    main()
