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
from rl.dqn.agent import (
    BATCH_SIZE,
    EPSILON_DECAY_STEPS,
    GAMMA,
    GRADIENT_CLIP_NORM,
    LEARNING_RATE,
    DQNAgent,
)
from rl.dqn.n_step import NStepAccumulator
from rl.dqn.replay_buffer import AnchoredReplayBuffer, ReplayBuffer

WARMUP_STEPS = 10_000
EVAL_INTERVAL = 10_000
EVAL_EPISODES = 5
ADAPTIVE_LR_FACTOR = 0.5
ADAPTIVE_MIN_LEARNING_RATE = 1e-5
ADAPTIVE_EXPLORATION_EPSILON = 0.15
ADAPTIVE_EXPLORATION_STEPS = 100_000
ADAPTIVE_MAX_ADJUSTMENTS = 3
CHAMPION_EVAL_EPISODES = 50
CHAMPION_MAX_EVAL_EPISODES = 200
CHAMPION_GATE_SEED = 7_000_000
CHAMPION_NOMINATION_RATIO = 0.9
CHAMPION_MIN_IMPROVEMENT = 10.0
CONFIDENCE_Z = 1.96


def choose_device(name: str) -> torch.device:
    if name == "auto":
        name = "cuda" if torch.cuda.is_available() else "cpu"
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
    return torch.device(name)


def evaluate_episodes(
    agent: DQNAgent,
    seed: int,
    episodes: int = EVAL_EPISODES,
) -> tuple[np.ndarray, np.ndarray]:
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
    return np.asarray(scores, dtype=np.float64), np.asarray(lengths, dtype=np.float64)


def evaluate(agent: DQNAgent, seed: int, episodes: int = EVAL_EPISODES) -> tuple[float, float]:
    scores, lengths = evaluate_episodes(agent, seed, episodes)
    return float(np.mean(scores)), float(np.mean(lengths))


def paired_gate_decision(
    candidate_scores: np.ndarray,
    incumbent_scores: np.ndarray,
    min_improvement: float = CHAMPION_MIN_IMPROVEMENT,
) -> dict[str, float | bool]:
    """Require a statistically credible paired improvement on identical seeds."""
    if candidate_scores.shape != incumbent_scores.shape or candidate_scores.size < 2:
        raise ValueError("paired champion evaluation requires equal arrays with at least 2 scores")
    differences = candidate_scores - incumbent_scores
    mean_difference = float(np.mean(differences))
    standard_error = float(np.std(differences, ddof=1) / math.sqrt(differences.size))
    lower_confidence = mean_difference - CONFIDENCE_Z * standard_error
    return {
        "candidate_mean": float(np.mean(candidate_scores)),
        "incumbent_mean": float(np.mean(incumbent_scores)),
        "mean_difference": mean_difference,
        "lower_confidence": lower_confidence,
        "accepted": lower_confidence > min_improvement,
    }


def should_expand_gate(
    gate: dict[str, float | bool],
    evaluated_episodes: int,
    max_episodes: int,
) -> bool:
    return (
        float(gate["mean_difference"]) > 0
        and max_episodes > evaluated_episodes
    )


def save_checkpoint(
    path: Path,
    agent: DQNAgent,
    best_eval_score: float,
    run_dir: Path,
    eval_episodes: int,
    paddle_hit_reward: float = 0.0,
    adaptive_patience_steps: int = 0,
    adaptive_adjustments: int = 0,
    last_improvement_step: int = 0,
    stall_paddle_hits: int = 0,
    stall_penalty: float = 0.0,
    learn_every: int = 1,
    anchor_steps: int = 0,
    anchor_fraction: float = 0.0,
    champion_eval_episodes: int = CHAMPION_EVAL_EPISODES,
    gate_attempts: int = 0,
    n_step: int = 1,
    priority_alpha: float = 0.0,
    champion_max_eval_episodes: int = CHAMPION_MAX_EVAL_EPISODES,
    curriculum_clear_max: float = 0.0,
    curriculum_prob: float = 1.0,
) -> None:
    payload = agent.checkpoint(best_eval_score=best_eval_score)
    payload["run_dir"] = str(run_dir)
    payload["curriculum_clear_max"] = curriculum_clear_max
    payload["curriculum_prob"] = curriculum_prob
    payload["eval_episodes"] = eval_episodes
    payload["paddle_hit_reward"] = paddle_hit_reward
    payload["adaptive_patience_steps"] = adaptive_patience_steps
    payload["adaptive_adjustments"] = adaptive_adjustments
    payload["last_improvement_step"] = last_improvement_step
    payload["stall_paddle_hits"] = stall_paddle_hits
    payload["stall_penalty"] = stall_penalty
    payload["learn_every"] = learn_every
    payload["anchor_steps"] = anchor_steps
    payload["anchor_fraction"] = anchor_fraction
    payload["champion_eval_episodes"] = champion_eval_episodes
    payload["gate_attempts"] = gate_attempts
    payload["n_step"] = n_step
    payload["priority_alpha"] = priority_alpha
    payload["champion_max_eval_episodes"] = champion_max_eval_episodes
    torch.save(payload, path)


def apply_plateau_adjustment(agent: DQNAgent) -> tuple[float, float]:
    """Cool learning and briefly reheat exploration after a score plateau."""
    old_learning_rate = agent.learning_rate
    new_learning_rate = max(
        ADAPTIVE_MIN_LEARNING_RATE,
        old_learning_rate * ADAPTIVE_LR_FACTOR,
    )
    agent.set_learning_rate(new_learning_rate)
    agent.start_exploration_boost(
        peak=ADAPTIVE_EXPLORATION_EPSILON,
        duration_steps=ADAPTIVE_EXPLORATION_STEPS,
    )
    return old_learning_rate, new_learning_rate


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
        "--paddle-hit-reward",
        type=float,
        help="extra learning reward per genuine paddle collision; fresh runs default to 0",
    )
    parser.add_argument(
        "--gradient-clip-norm",
        type=float,
        help="maximum global gradient norm; fresh runs default to 10",
    )
    parser.add_argument(
        "--stall-paddle-hits",
        type=int,
        help="consecutive paddle contacts without a brick hit before applying a stall penalty",
    )
    parser.add_argument(
        "--stall-penalty",
        type=float,
        help="positive reward amount subtracted at each no-brick paddle-hit threshold",
    )
    parser.add_argument(
        "--adaptive-patience-steps",
        type=int,
        help="after this many steps without a new eval best, halve LR and briefly "
        "raise exploration; 0 disables, fresh runs default to disabled",
    )
    parser.add_argument(
        "--learn-every",
        type=int,
        help="perform one gradient update every N environment decisions; fresh runs default to 1",
    )
    parser.add_argument(
        "--anchor-steps",
        type=int,
        help="on resume, collect this many protected champion transitions before learning",
    )
    parser.add_argument(
        "--anchor-fraction",
        type=float,
        help="fraction of each replay batch drawn from protected champion transitions",
    )
    parser.add_argument(
        "--champion-eval-episodes",
        type=int,
        help="initial paired holdout episodes for champion candidates; default 50",
    )
    parser.add_argument(
        "--champion-max-eval-episodes",
        type=int,
        help="confirm every positive paired gate on this many episodes before promotion; default 200",
    )
    parser.add_argument(
        "--n-step",
        type=int,
        help="number of rewards accumulated into each replay transition; default 1",
    )
    parser.add_argument(
        "--priority-alpha",
        type=float,
        help="prioritized replay strength in [0, 1]; 0 is uniform, recommended experiment 0.6",
    )
    parser.add_argument(
        "--curriculum-clear-max",
        type=float,
        help="train only: pre-clear up to this fraction of bricks at each reset so the agent "
        "sees fast-ball mid/late-game states; 0 disables, fresh runs default to 0, recommended 0.6",
    )
    parser.add_argument(
        "--curriculum-prob",
        type=float,
        help="train only: probability the curriculum is applied per reset; the rest stay full-board "
        "openings so the agent keeps practising the start. 1.0 (default) always applies; mix with ~0.5",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        help="reward discount factor in (0, 1); fresh runs default to 0.99, longer-horizon "
        "experiments use ~0.997",
    )
    parser.add_argument(
        "--epsilon-decay-steps",
        type=int,
        help="steps to linearly anneal exploration epsilon 1.0->0.05; fresh runs default to 150000",
    )
    parser.add_argument(
        "--reset-optimizer",
        action="store_true",
        help="discard saved Adam moments after loading a checkpoint; useful for a new fine-tuning fork",
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
    if args.paddle_hit_reward is not None and (
        not math.isfinite(args.paddle_hit_reward) or args.paddle_hit_reward < 0
    ):
        parser.error("--paddle-hit-reward must be a finite non-negative number")
    if args.gradient_clip_norm is not None and (
        not math.isfinite(args.gradient_clip_norm) or args.gradient_clip_norm <= 0
    ):
        parser.error("--gradient-clip-norm must be a positive finite number")
    if args.stall_paddle_hits is not None and args.stall_paddle_hits <= 0:
        parser.error("--stall-paddle-hits must be positive")
    if args.stall_penalty is not None and (
        not math.isfinite(args.stall_penalty) or args.stall_penalty <= 0
    ):
        parser.error("--stall-penalty must be a positive finite number")
    if (args.stall_paddle_hits is None) != (args.stall_penalty is None):
        parser.error("--stall-paddle-hits and --stall-penalty must be supplied together")
    if args.adaptive_patience_steps is not None and args.adaptive_patience_steps < 0:
        parser.error("--adaptive-patience-steps must be non-negative")
    if args.learn_every is not None and args.learn_every <= 0:
        parser.error("--learn-every must be positive")
    if args.anchor_steps is not None and args.anchor_steps < 0:
        parser.error("--anchor-steps must be non-negative")
    if args.anchor_fraction is not None and not 0.0 <= args.anchor_fraction < 1.0:
        parser.error("--anchor-fraction must be in [0, 1)")
    if args.champion_eval_episodes is not None and args.champion_eval_episodes < 2:
        parser.error("--champion-eval-episodes must be at least 2")
    if args.champion_max_eval_episodes is not None and args.champion_max_eval_episodes < 2:
        parser.error("--champion-max-eval-episodes must be at least 2")
    if args.n_step is not None and args.n_step <= 0:
        parser.error("--n-step must be positive")
    if args.priority_alpha is not None and not 0.0 <= args.priority_alpha <= 1.0:
        parser.error("--priority-alpha must be in [0, 1]")
    if args.curriculum_clear_max is not None and (
        not math.isfinite(args.curriculum_clear_max)
        or not 0.0 <= args.curriculum_clear_max < 1.0
    ):
        parser.error("--curriculum-clear-max must be in [0, 1)")
    if args.curriculum_prob is not None and (
        not math.isfinite(args.curriculum_prob) or not 0.0 <= args.curriculum_prob <= 1.0
    ):
        parser.error("--curriculum-prob must be in [0, 1]")
    if args.gamma is not None and (not math.isfinite(args.gamma) or not 0.0 < args.gamma < 1.0):
        parser.error("--gamma must be in (0, 1)")
    if args.epsilon_decay_steps is not None and args.epsilon_decay_steps <= 0:
        parser.error("--epsilon-decay-steps must be positive")
    if args.fork_run and not args.resume:
        parser.error("--fork-run requires --resume")
    if args.reset_optimizer and not args.resume:
        parser.error("--reset-optimizer requires --resume")

    device = choose_device(args.device)
    agent = DQNAgent(
        device,
        seed=args.seed,
        learning_rate=args.learning_rate or LEARNING_RATE,
        gradient_clip_norm=args.gradient_clip_norm or GRADIENT_CLIP_NORM,
    )
    start_step = 0
    best_eval_score = float("-inf")
    eval_episodes = args.eval_episodes or EVAL_EPISODES
    paddle_hit_reward = args.paddle_hit_reward or 0.0
    stall_paddle_hits = args.stall_paddle_hits or 0
    stall_penalty = args.stall_penalty or 0.0
    adaptive_patience_steps = args.adaptive_patience_steps or 0
    adaptive_adjustments = 0
    last_improvement_step = 0
    learn_every = args.learn_every or 1
    anchor_steps = args.anchor_steps or 0
    anchor_fraction = args.anchor_fraction if args.anchor_fraction is not None else 0.25
    champion_eval_episodes = args.champion_eval_episodes or CHAMPION_EVAL_EPISODES
    champion_max_eval_episodes = (
        args.champion_max_eval_episodes or CHAMPION_MAX_EVAL_EPISODES
    )
    gate_attempts = 0
    n_step = args.n_step or 1
    priority_alpha = args.priority_alpha if args.priority_alpha is not None else 0.0
    curriculum_clear_max = args.curriculum_clear_max or 0.0
    curriculum_prob = args.curriculum_prob if args.curriculum_prob is not None else 1.0
    gamma = args.gamma or GAMMA
    epsilon_decay_steps = args.epsilon_decay_steps or EPSILON_DECAY_STEPS
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
        checkpoint_paddle_hit_reward = float(checkpoint.get("paddle_hit_reward", 0.0))
        if args.paddle_hit_reward is None:
            paddle_hit_reward = checkpoint_paddle_hit_reward
        elif (
            not math.isclose(
                args.paddle_hit_reward,
                checkpoint_paddle_hit_reward,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            and not args.fork_run
        ):
            parser.error(
                "changing --paddle-hit-reward on a resumed log requires --fork-run"
            )
        checkpoint_stall_paddle_hits = int(checkpoint.get("stall_paddle_hits", 0))
        checkpoint_stall_penalty = float(checkpoint.get("stall_penalty", 0.0))
        if args.stall_paddle_hits is None:
            stall_paddle_hits = checkpoint_stall_paddle_hits
            stall_penalty = checkpoint_stall_penalty
        elif (
            args.stall_paddle_hits != checkpoint_stall_paddle_hits
            or not math.isclose(
                args.stall_penalty,
                checkpoint_stall_penalty,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ) and not args.fork_run:
            parser.error("changing stall shaping on a resumed log requires --fork-run")
        checkpoint_adaptive_patience = int(
            checkpoint.get("adaptive_patience_steps", 0)
        )
        adaptive_setting_changed = (
            args.adaptive_patience_steps is not None
            and args.adaptive_patience_steps != checkpoint_adaptive_patience
        )
        if args.adaptive_patience_steps is None:
            adaptive_patience_steps = checkpoint_adaptive_patience
        adaptive_adjustments = int(checkpoint.get("adaptive_adjustments", 0))
        last_improvement_step = int(
            checkpoint.get("last_improvement_step", checkpoint["agent_steps"])
        )
        if args.learn_every is None:
            learn_every = int(checkpoint.get("learn_every", 1))
        if args.anchor_steps is None:
            anchor_steps = int(checkpoint.get("anchor_steps", 0))
        if args.anchor_fraction is None:
            anchor_fraction = float(checkpoint.get("anchor_fraction", 0.25))
        if args.champion_eval_episodes is None:
            champion_eval_episodes = int(
                checkpoint.get("champion_eval_episodes", CHAMPION_EVAL_EPISODES)
            )
        if args.champion_max_eval_episodes is None:
            champion_max_eval_episodes = int(
                checkpoint.get(
                    "champion_max_eval_episodes", CHAMPION_MAX_EVAL_EPISODES
                )
            )
        gate_attempts = int(checkpoint.get("gate_attempts", 0))
        if args.n_step is None:
            n_step = int(checkpoint.get("n_step", 1))
        if args.priority_alpha is None:
            priority_alpha = float(checkpoint.get("priority_alpha", 0.0))
        checkpoint_curriculum = float(checkpoint.get("curriculum_clear_max", 0.0))
        if args.curriculum_clear_max is None:
            curriculum_clear_max = checkpoint_curriculum
        elif (
            not math.isclose(
                args.curriculum_clear_max,
                checkpoint_curriculum,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            and not args.fork_run
        ):
            parser.error(
                "changing --curriculum-clear-max on a resumed log requires --fork-run"
            )
        checkpoint_curriculum_prob = float(checkpoint.get("curriculum_prob", 1.0))
        if args.curriculum_prob is None:
            curriculum_prob = checkpoint_curriculum_prob
        elif (
            not math.isclose(
                args.curriculum_prob,
                checkpoint_curriculum_prob,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            and not args.fork_run
        ):
            parser.error(
                "changing --curriculum-prob on a resumed log requires --fork-run"
            )
        checkpoint_gamma = float(checkpoint.get("gamma", GAMMA))
        if args.gamma is None:
            gamma = checkpoint_gamma
        elif (
            not math.isclose(args.gamma, checkpoint_gamma, rel_tol=0.0, abs_tol=1e-12)
            and not args.fork_run
        ):
            parser.error("changing --gamma on a resumed log requires --fork-run")
        # Exploration schedule is forward-looking only, so overriding it needs no fork.
        if args.epsilon_decay_steps is None:
            epsilon_decay_steps = int(
                checkpoint.get("epsilon_decay_steps", EPSILON_DECAY_STEPS)
            )
        if args.learning_rate is not None:
            agent.set_learning_rate(args.learning_rate)
        if args.gradient_clip_norm is not None:
            agent.set_gradient_clip_norm(args.gradient_clip_norm)
        if args.reset_optimizer:
            agent.reset_optimizer()
        start_step = agent.agent_steps
        best_eval_score = float(checkpoint.get("best_eval_score", float("-inf")))
        run_dir = (
            create_run_dir()
            if args.fork_run
            else Path(checkpoint.get("run_dir", args.resume.parent))
        )
        if args.fork_run or adaptive_setting_changed:
            adaptive_adjustments = 0
            last_improvement_step = start_step
        if args.fork_run:
            gate_attempts = 0
    else:
        run_dir = create_run_dir()
    # The resolved discount/exploration schedule are authoritative for this run, overriding
    # whatever load_checkpoint restored (a plain resume resolves back to the same values).
    agent.gamma = gamma
    agent.epsilon_decay_steps = epsilon_decay_steps
    if champion_max_eval_episodes < champion_eval_episodes:
        parser.error("--champion-max-eval-episodes must be >= --champion-eval-episodes")
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "log.csv"
    adaptive_log_path = run_dir / "adaptive.csv"
    gate_log_path = run_dir / "gates.csv"
    if not log_path.exists():
        with log_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                ("steps", "epsilon", "loss", "train_return", "eval_score_mean", "eval_len_mean")
            )
    if adaptive_patience_steps and not adaptive_log_path.exists():
        with adaptive_log_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                (
                    "steps",
                    "event",
                    "old_learning_rate",
                    "new_learning_rate",
                    "exploration_peak",
                    "exploration_steps",
                    "best_eval_score",
                )
            )
    if not gate_log_path.exists():
        with gate_log_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                (
                    "steps",
                    "attempt",
                    "quick_score",
                    "candidate_mean",
                    "incumbent_mean",
                    "mean_difference",
                    "lower_95",
                    "episodes",
                    "accepted",
                    "archived",
                )
            )

    print(
        f"Run: {run_dir} | start={start_step} | add={args.steps} | "
        f"lr={agent.learning_rate:g} | eval_episodes={eval_episodes} | "
        f"paddle_reward={paddle_hit_reward:g} | "
        f"stall={stall_paddle_hits or 'off'}/{stall_penalty:g} | "
        f"grad_clip={agent.gradient_clip_norm or 'off'} | "
        f"adaptive_patience={adaptive_patience_steps or 'off'} | "
        f"learn_every={learn_every} | anchor={anchor_steps}/{anchor_fraction:g} | "
        f"champion_eval={champion_eval_episodes}->{champion_max_eval_episodes} | "
        f"n_step={n_step} | priority_alpha={priority_alpha:g} | "
        f"gamma={gamma:g} | eps_decay={epsilon_decay_steps} | "
        f"curriculum={curriculum_clear_max:g}@p{curriculum_prob:g} | "
        f"reset_optimizer={args.reset_optimizer} | fork={args.fork_run} | "
        f"live_export={args.live_export}"
    )

    if args.fork_run:
        # Quick scores remain useful diagnostics, but only a larger fixed
        # holdout establishes the branch's incumbent champion.
        baseline_quick_score, baseline_length = evaluate(
            agent, args.seed + agent.agent_steps, eval_episodes
        )
        baseline_scores, _ = evaluate_episodes(
            agent,
            CHAMPION_GATE_SEED + args.seed,
            champion_eval_episodes,
        )
        best_eval_score = float(np.mean(baseline_scores))
        last_improvement_step = agent.agent_steps
        with log_path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                (
                    agent.agent_steps,
                    agent.epsilon,
                    float("nan"),
                    float("nan"),
                    baseline_quick_score,
                    baseline_length,
                )
            )
        with gate_log_path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                (
                    agent.agent_steps,
                    0,
                    baseline_quick_score,
                    best_eval_score,
                    best_eval_score,
                    0.0,
                    0.0,
                    champion_eval_episodes,
                    True,
                    False,
                )
            )
        save_checkpoint(
            run_dir / "latest.pt",
            agent,
            best_eval_score,
            run_dir,
            eval_episodes,
            paddle_hit_reward,
            adaptive_patience_steps,
            adaptive_adjustments,
            last_improvement_step,
            stall_paddle_hits,
            stall_penalty,
            learn_every,
            anchor_steps,
            anchor_fraction,
            champion_eval_episodes,
            gate_attempts,
            n_step,
            priority_alpha,
            champion_max_eval_episodes,
            curriculum_clear_max,
            curriculum_prob,
        )
        save_checkpoint(
            run_dir / "best.pt",
            agent,
            best_eval_score,
            run_dir,
            eval_episodes,
            paddle_hit_reward,
            adaptive_patience_steps,
            adaptive_adjustments,
            last_improvement_step,
            stall_paddle_hits,
            stall_penalty,
            learn_every,
            anchor_steps,
            anchor_fraction,
            champion_eval_episodes,
            gate_attempts,
            n_step,
            priority_alpha,
            champion_max_eval_episodes,
            curriculum_clear_max,
            curriculum_prob,
        )
        if args.live_export:
            publish_best(agent, best_eval_score)
        print(
            f"Fork baseline: quick={baseline_quick_score:.1f}, "
            f"champion_{champion_eval_episodes}={best_eval_score:.1f}, "
            f"length={baseline_length:.1f}"
        )

    best_checkpoint_path = run_dir / "best.pt"
    anchor_agent: DQNAgent | None = None
    if args.resume and anchor_steps:
        anchor_checkpoint_path = (
            best_checkpoint_path if best_checkpoint_path.exists() else args.resume
        )
        anchor_checkpoint = torch.load(
            anchor_checkpoint_path, map_location=device, weights_only=False
        )
        anchor_agent = DQNAgent(device, seed=args.seed + 73)
        anchor_agent.load_checkpoint(anchor_checkpoint)

    env = BreakoutEnv(
        paddle_hit_reward=paddle_hit_reward,
        stall_paddle_hits=stall_paddle_hits,
        stall_penalty=stall_penalty,
        curriculum_clear_max=curriculum_clear_max,
        curriculum_prob=curriculum_prob,
    )
    observation, _ = env.reset(seed=args.seed)
    replay = (
        AnchoredReplayBuffer(
            anchor_fraction=anchor_fraction,
            priority_alpha=priority_alpha,
        )
        if anchor_agent is not None
        else ReplayBuffer(priority_alpha=priority_alpha)
    )
    n_step_accumulator = NStepAccumulator(n_step, gamma)
    episode_return = 0.0
    recent_returns: list[float] = []
    losses: list[float] = []
    rng = np.random.default_rng(args.seed + 1)
    target_step = start_step + args.steps
    # Normal runs use the pinned 10k warmup. A deliberately short smoke run
    # gets enough post-warmup updates to verify loss/checkpoint plumbing.
    warmup = (
        anchor_steps
        if anchor_agent is not None
        else WARMUP_STEPS if args.steps > WARMUP_STEPS else max(BATCH_SIZE, args.steps // 5)
    )

    progress = tqdm(total=args.steps, desc=f"DQN ({device.type})")
    while agent.agent_steps < target_step:
        relative_step = agent.agent_steps - start_step
        collecting_anchor = anchor_agent is not None and relative_step < warmup
        if relative_step < warmup:
            if collecting_anchor:
                action = anchor_agent.act(observation)
                agent.agent_steps += 1
            elif args.resume:
                # A trained policy should refill its empty replay buffer from
                # its own state distribution. Fully random warmup data causes
                # destructive off-policy updates during fine-tuning.
                action = agent.act(observation)
            else:
                action = int(rng.integers(0, 3))
                agent.agent_steps += 1
        else:
            action = agent.act(observation)
        next_observation, reward, terminated, truncated, _ = env.step(action)
        done = terminated or truncated
        replay_transitions = n_step_accumulator.add(
            observation,
            action,
            reward,
            next_observation,
            done,
            anchor=collecting_anchor,
        )
        for transition in replay_transitions:
            add_transition = (
                replay.add_anchor
                if transition.anchor and hasattr(replay, "add_anchor")
                else replay.add
            )
            add_transition(
                transition.observation,
                transition.action,
                transition.reward,
                transition.next_observation,
                transition.done,
                transition.discount,
            )
        observation = next_observation
        episode_return += reward

        if (
            agent.agent_steps - start_step >= warmup
            and len(replay) >= BATCH_SIZE
            and agent.agent_steps % learn_every == 0
        ):
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
            accepted_champion = False
            nomination_threshold = (
                best_eval_score * CHAMPION_NOMINATION_RATIO
                if math.isfinite(best_eval_score) and best_eval_score > 0
                else float("-inf")
            )
            if (
                relative_step >= warmup
                and (
                    not best_checkpoint_path.exists()
                    or eval_score >= nomination_threshold
                )
            ):
                gate_attempts += 1
                gate_seed = (
                    CHAMPION_GATE_SEED
                    + 1_000_000
                    + args.seed
                    + gate_attempts * champion_max_eval_episodes
                )
                evaluated_episodes = champion_eval_episodes
                archived_nominee = False
                candidate_scores, _ = evaluate_episodes(
                    agent, gate_seed, champion_eval_episodes
                )
                if best_checkpoint_path.exists():
                    incumbent_checkpoint = torch.load(
                        best_checkpoint_path,
                        map_location=device,
                        weights_only=False,
                    )
                    incumbent_agent = DQNAgent(device, seed=args.seed + gate_attempts)
                    incumbent_agent.load_checkpoint(incumbent_checkpoint)
                    incumbent_scores, _ = evaluate_episodes(
                        incumbent_agent, gate_seed, champion_eval_episodes
                    )
                    gate = paired_gate_decision(candidate_scores, incumbent_scores)
                    if should_expand_gate(
                        gate,
                        champion_eval_episodes,
                        champion_max_eval_episodes,
                    ):
                        extra_episodes = (
                            champion_max_eval_episodes - champion_eval_episodes
                        )
                        extra_candidate_scores, _ = evaluate_episodes(
                            agent,
                            gate_seed + champion_eval_episodes,
                            extra_episodes,
                        )
                        extra_incumbent_scores, _ = evaluate_episodes(
                            incumbent_agent,
                            gate_seed + champion_eval_episodes,
                            extra_episodes,
                        )
                        candidate_scores = np.concatenate(
                            (candidate_scores, extra_candidate_scores)
                        )
                        incumbent_scores = np.concatenate(
                            (incumbent_scores, extra_incumbent_scores)
                        )
                        evaluated_episodes = champion_max_eval_episodes
                        gate = paired_gate_decision(
                            candidate_scores, incumbent_scores
                        )
                    del incumbent_agent
                else:
                    candidate_mean = float(np.mean(candidate_scores))
                    gate = {
                        "candidate_mean": candidate_mean,
                        "incumbent_mean": float("nan"),
                        "mean_difference": float("nan"),
                        "lower_confidence": float("nan"),
                        "accepted": True,
                    }
                accepted_champion = bool(gate["accepted"])
                if (
                    not accepted_champion
                    and math.isfinite(float(gate["mean_difference"]))
                    and float(gate["mean_difference"]) > 0
                ):
                    nominee_dir = run_dir / "nominees"
                    nominee_dir.mkdir(exist_ok=True)
                    save_checkpoint(
                        nominee_dir / f"step-{agent.agent_steps}.pt",
                        agent,
                        float(gate["candidate_mean"]),
                        run_dir,
                        eval_episodes,
                        paddle_hit_reward,
                        adaptive_patience_steps,
                        adaptive_adjustments,
                        last_improvement_step,
                        stall_paddle_hits,
                        stall_penalty,
                        learn_every,
                        anchor_steps,
                        anchor_fraction,
                        champion_eval_episodes,
                        gate_attempts,
                        n_step,
                        priority_alpha,
                        champion_max_eval_episodes,
                        curriculum_clear_max,
                        curriculum_prob,
                    )
                    archived_nominee = True
                with gate_log_path.open("a", newline="", encoding="utf-8") as handle:
                    csv.writer(handle).writerow(
                        (
                            agent.agent_steps,
                            gate_attempts,
                            eval_score,
                            gate["candidate_mean"],
                            gate["incumbent_mean"],
                            gate["mean_difference"],
                            gate["lower_confidence"],
                            evaluated_episodes,
                            accepted_champion,
                            archived_nominee,
                        )
                    )
                progress.write(
                    f"Champion gate {gate_attempts}: candidate="
                    f"{float(gate['candidate_mean']):.1f}, incumbent="
                    f"{float(gate['incumbent_mean']):.1f}, lower95="
                    f"{float(gate['lower_confidence']):.1f}, "
                    f"episodes={evaluated_episodes}, accepted={accepted_champion}, "
                    f"archived={archived_nominee}"
                )
                if accepted_champion:
                    best_eval_score = float(gate["candidate_mean"])
                    last_improvement_step = agent.agent_steps

            if not accepted_champion and (
                adaptive_patience_steps
                and adaptive_adjustments < ADAPTIVE_MAX_ADJUSTMENTS
                and agent.agent_steps - last_improvement_step
                >= adaptive_patience_steps
            ):
                old_lr, new_lr = apply_plateau_adjustment(agent)
                adaptive_adjustments += 1
                last_improvement_step = agent.agent_steps
                with adaptive_log_path.open("a", newline="", encoding="utf-8") as handle:
                    csv.writer(handle).writerow(
                        (
                            agent.agent_steps,
                            adaptive_adjustments,
                            old_lr,
                            new_lr,
                            ADAPTIVE_EXPLORATION_EPSILON,
                            ADAPTIVE_EXPLORATION_STEPS,
                            best_eval_score,
                        )
                    )
                progress.write(
                    f"Plateau adjustment {adaptive_adjustments}/"
                    f"{ADAPTIVE_MAX_ADJUSTMENTS}: lr {old_lr:g} -> {new_lr:g}, "
                    f"epsilon reheated to {ADAPTIVE_EXPLORATION_EPSILON:g} for "
                    f"{ADAPTIVE_EXPLORATION_STEPS} steps"
                )
            save_checkpoint(
                run_dir / "latest.pt",
                agent,
                best_eval_score,
                run_dir,
                eval_episodes,
                paddle_hit_reward,
                adaptive_patience_steps,
                adaptive_adjustments,
                last_improvement_step,
                stall_paddle_hits,
                stall_penalty,
                learn_every,
                anchor_steps,
                anchor_fraction,
                champion_eval_episodes,
                gate_attempts,
                n_step,
                priority_alpha,
                champion_max_eval_episodes,
                curriculum_clear_max,
                curriculum_prob,
            )
            if accepted_champion:
                save_checkpoint(
                    run_dir / "best.pt",
                    agent,
                    best_eval_score,
                    run_dir,
                    eval_episodes,
                    paddle_hit_reward,
                    adaptive_patience_steps,
                    adaptive_adjustments,
                    last_improvement_step,
                    stall_paddle_hits,
                    stall_penalty,
                    learn_every,
                    anchor_steps,
                    anchor_fraction,
                    champion_eval_episodes,
                    gate_attempts,
                    n_step,
                    priority_alpha,
                    champion_max_eval_episodes,
                    curriculum_clear_max,
                    curriculum_prob,
                )
                if args.live_export:
                    # Publish only improved bests (never latest.pt), atomically,
                    # so the running app hot-reloads without a restart. Checkpoint
                    # selection above is unchanged.
                    publish_best(agent, best_eval_score)
            progress.set_postfix(
                loss=f"{mean_loss:.4f}",
                eval=f"{eval_score:.0f}",
                champion=f"{best_eval_score:.0f}",
            )
        progress.update(1)
    progress.close()
    print(f"Run saved to {run_dir}")


if __name__ == "__main__":
    main()
