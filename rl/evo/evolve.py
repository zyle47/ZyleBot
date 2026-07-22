"""Champion-seeded neuroevolution loop for Breakout (CLI: ``python -m rl.evo.evolve``).

Each generation:
  1. evaluate the population on that generation's shared *training* seeds (fair,
     common-random-numbers, decorrelated across generations),
  2. score the generation's best genome on a fixed *validation* seed set so the
     "all-time best" curve is comparable across generations and not seed-luck,
  3. breed the next generation (elitism + tournament + crossover + mutation).

On each validation improvement the best genome is saved to the run dir and,
with ``--live-export``, atomically published to ``rl/policy_evo/`` for the live
viewer to play. The deployed champion in ``rl/policy/`` is never touched here.

When the run ends (target generations reached or Ctrl-C), the best genome faces a
paired audit against the seed champion on the same +10 lower-95 bar as every DQN
champion. Promotion to the live game stays a manual, gated step.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from rl.evo import genome as g
from rl.evo.evaluate import evaluate_population, genome_fitness
from rl.evo.population import EvoConfig, init_population, next_generation

DEFAULT_CHAMPION = Path("rl/policy/breakout_policy.npz")
EVO_DIR = Path("rl/policy_evo")  # the live-viewer slot, separate from the deployed champion
STATUS_PATH = EVO_DIR / "status.json"
VIEWER_SLOTS = 6  # top-N genomes published for the 6-brain arena
HISTORY_CAP = 400  # generations kept in status.json for the chart
VALIDATION_SEED_TAG = 999_983  # fixed, distinct from any generation's training seeds
AUDIT_SEED_OFFSET = 100_000  # mirrors rl.train.evaluate_episodes


def create_run_dir(root: Path = Path("rl/runs_evo"), timestamp: str | None = None) -> Path:
    """A new timestamped run dir, suffixed on same-second collisions."""
    stem = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = root / stem
    suffix = 2
    while candidate.exists():
        candidate = root / f"{stem}-{suffix}"
        suffix += 1
    return candidate


def generation_seeds(base_seed: int, generation: int, count: int) -> tuple[int, ...]:
    """Deterministic, decorrelated training seeds for one generation."""
    rng = np.random.default_rng([base_seed, generation])
    return tuple(int(s) for s in rng.integers(1, 2**31 - 1, size=count))


def validation_seeds(base_seed: int, count: int, rotation: int = 0) -> tuple[int, ...]:
    """A holdout seed set used to score every generation's best genome.

    ``rotation`` selects a different set. Rotating periodically (and re-scoring the
    incumbent on the new set) stops a long run from silently overfitting one fixed
    set of boards — measured on run 20260721-180231, where validation drifted +239
    while the audited truth moved +11.
    """
    rng = np.random.default_rng([base_seed, VALIDATION_SEED_TAG, rotation])
    return tuple(int(s) for s in rng.integers(1, 2**31 - 1, size=count))


def _try_fs(action, attempts: int = 6, delay: float = 0.12) -> bool:
    """Run a filesystem action, retrying past transient Windows locks.

    On Windows ``os.replace`` fails with ``PermissionError`` (WinError 5) when the
    destination is open by another process — which is *expected* here: the app
    reads slot files while the boards play, and Defender / the editor watcher scan
    the workspace. Viewer publishing is cosmetic, so we retry briefly and then give
    up gracefully rather than let a dropped frame crash a long run.
    """
    for attempt in range(attempts):
        try:
            action()
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(delay)
    return False


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON via temp-file + os.replace so a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-status-", suffix=".json")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None  # type: ignore[assignment]
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def publish_viewer_state(
    population: list[np.ndarray],
    fitnesses: np.ndarray,
    order: np.ndarray,
    generation: int,
    sigma: float,
    best_val: float,
    baseline: float,
    history: list[dict],
) -> bool:
    """Publish the generation's top-N genomes (slot0..slotN-1) plus status.json.

    Each slot is an ordinary policy dir the app hot-reloads. status.json is written
    LAST as the generation's commit marker; the six .npz commits are independently
    atomic, so a reader sees at most a <1s cross-slot skew, harmless for a viewer.
    Returns False (frame dropped) instead of raising if a transient file lock
    survives the retries — the caller keeps evolving. Lazy import keeps the loop
    torch-free.
    """
    from rl.export_policy import publish_policy

    slots = []
    for slot in range(min(VIEWER_SLOTS, len(population))):
        idx = int(order[slot])
        fitness = float(fitnesses[idx])
        arrays = g.unflatten(population[idx])
        output = EVO_DIR / f"slot{slot}" / "breakout_policy.npz"
        ok = _try_fs(
            lambda a=arrays, f=fitness, o=output: publish_policy(
                a, training_steps=generation, eval_score=f, output=o
            )
        )
        if not ok:
            return False  # drop the whole frame; status.json still points at the last good gen
        slots.append({"rank": slot + 1, "fitness": fitness})

    payload = {
        "available": True,
        "generation": generation,
        "all_time_best": float(best_val),
        "baseline": float(baseline),
        "train_best": float(fitnesses.max()),
        "train_mean": float(fitnesses.mean()),
        "sigma": float(sigma),
        "slots": slots,
        "history": history[-HISTORY_CAP:],
    }
    return _try_fs(lambda: _atomic_write_json(STATUS_PATH, payload))


def paired_audit(
    candidate: np.ndarray,
    champion: np.ndarray,
    episodes: int,
    seed: int,
) -> dict[str, float | bool]:
    """Paired common-random-numbers A/B on identical seeds, canonical +10 gate."""
    from rl.train import paired_gate_decision  # lazy; reuses the one true gate

    seeds = tuple(seed + AUDIT_SEED_OFFSET + i for i in range(episodes))
    _, candidate_scores = genome_fitness(candidate, seeds)
    _, champion_scores = genome_fitness(champion, seeds)
    gate = paired_gate_decision(candidate_scores, champion_scores)
    return gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Champion-seeded Breakout neuroevolution")
    parser.add_argument("--generations", type=int, default=100)
    parser.add_argument("--population", type=int, default=60)
    parser.add_argument("--elite", type=int, default=3)
    parser.add_argument("--tournament", type=int, default=3)
    parser.add_argument("--crossover-rate", type=float, default=0.5)
    parser.add_argument("--mutation-rate", type=float, default=1.0)
    parser.add_argument("--sigma", type=float, default=0.02, help="initial mutation std")
    parser.add_argument("--sigma-decay", type=float, default=0.995)
    parser.add_argument("--sigma-min", type=float, default=0.002)
    parser.add_argument("--eval-seeds", type=int, default=8, help="training rollouts per genome/gen")
    parser.add_argument("--val-seeds", type=int, default=16, help="validation rollouts per check")
    parser.add_argument(
        "--val-rotate-every",
        type=int,
        default=0,
        help="redraw the validation seed set every N generations and re-score the current best on it, "
        "so all_time_best cannot drift by overfitting one fixed set. 0 disables (default); try 50. "
        "NOTE: all_time_best may DROP at a rotation — that is an honest re-measurement, not a bug.",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1, help="parallel eval processes")
    parser.add_argument("--champion", type=Path, default=DEFAULT_CHAMPION)
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="random init instead of seeding the champion (far weaker; authentic evolve-from-zero)",
    )
    parser.add_argument("--run-dir", type=Path, help="override the auto-timestamped run dir")
    parser.add_argument(
        "--live-export",
        action="store_true",
        help="publish the generation's top-6 genomes + status.json to rl/policy_evo/ for the live viewer",
    )
    parser.add_argument(
        "--viewer-interval",
        type=float,
        default=0.75,
        help="minimum seconds between live-viewer publishes (bounds disk churn)",
    )
    parser.add_argument("--audit-episodes", type=int, default=200, help="final paired audit; 0 skips")
    parser.add_argument("--audit-seed", type=int, default=42)
    parser.add_argument(
        "--curriculum-clear-max",
        type=float,
        default=0.0,
        help="TRAINING fitness only: pre-clear up to this fraction of the wall at reset (and advance "
        "ball speed to match) so genomes practise the fast mid/late game. 0 disables; try 0.6. "
        "Validation always uses a plain full board, so the reported score stays comparable.",
    )
    parser.add_argument(
        "--curriculum-prob",
        type=float,
        default=0.5,
        help="probability the curriculum applies per training reset; the rest stay full-board openings. "
        "MIX (~0.5) — 1.0 starves the opening and has killed runs before. No-op unless clear-max > 0.",
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.generations <= 0:
        parser.error("--generations must be positive")
    if args.population < 2:
        parser.error("--population must be at least 2")
    if not 0 <= args.elite < args.population:
        parser.error("--elite must be in [0, population)")
    if args.tournament < 1:
        parser.error("--tournament must be at least 1")
    if not 0.0 <= args.crossover_rate <= 1.0:
        parser.error("--crossover-rate must be in [0, 1]")
    if not 0.0 < args.mutation_rate <= 1.0:
        parser.error("--mutation-rate must be in (0, 1]")
    if args.sigma <= 0 or args.sigma_min <= 0 or args.sigma_min > args.sigma:
        parser.error("require 0 < --sigma-min <= --sigma")
    if not 0.0 < args.sigma_decay <= 1.0:
        parser.error("--sigma-decay must be in (0, 1]")
    if args.eval_seeds < 1 or args.val_seeds < 1:
        parser.error("--eval-seeds and --val-seeds must be positive")
    if args.audit_episodes < 0 or args.audit_episodes == 1:
        parser.error("--audit-episodes must be 0 or >= 2")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if not 0.0 <= args.curriculum_clear_max < 1.0:
        parser.error("--curriculum-clear-max must be in [0, 1)")
    if not 0.0 <= args.curriculum_prob <= 1.0:
        parser.error("--curriculum-prob must be in [0, 1]")
    if args.val_rotate_every < 0:
        parser.error("--val-rotate-every must be non-negative")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)

    config = EvoConfig(
        population=args.population,
        elite=args.elite,
        tournament=args.tournament,
        crossover_rate=args.crossover_rate,
        mutation_rate=args.mutation_rate,
        sigma_init=args.sigma,
        sigma_decay=args.sigma_decay,
        sigma_min=args.sigma_min,
    )
    rng = np.random.default_rng(args.seed)

    if args.from_scratch:
        seed_vector = g.random_genome(rng)
        seed_label = "random"
    else:
        # load_genome accepts a published .npz policy OR a raw .npy genome, so a run
        # can be continued from a previous run's best.npy without deploying it first.
        seed_vector = g.load_genome(args.champion)
        seed_label = str(args.champion)

    run_dir = args.run_dir or create_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    run_metadata = {
        "run_id": run_dir.name,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "seed": args.seed,
        "seed_genome": seed_label,
        "generations": args.generations,
        "population": args.population,
        "eval_seeds": args.eval_seeds,
        "val_seeds": args.val_seeds,
        "val_rotate_every": args.val_rotate_every,
        "curriculum_clear_max": args.curriculum_clear_max,
        "curriculum_prob": args.curriculum_prob,
        "live_export": args.live_export,
    }
    _atomic_write_json(run_dir / "run.json", run_metadata)
    log_path = run_dir / "evo_log.csv"
    with log_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(
            (
                "generation",
                "sigma",
                "train_best",
                "train_mean",
                "train_worst",
                "val_score",
                "all_time_best",
                "seconds",
                "val_rotation",
            )
        )

    val_rotation = 0
    val_seeds = validation_seeds(args.seed, args.val_seeds, val_rotation)
    population = init_population(seed_vector, config.population, config.sigma_at(0), rng)
    history: list[dict] = []
    last_publish = 0.0

    # Baseline: the seed genome's fixed-validation score. best.npz starts here so
    # nothing weaker than the champion is ever saved or published.
    best_val, _ = genome_fitness(seed_vector, val_seeds)
    baseline_val = best_val  # the champion's fixed-validation score — the "line to beat"
    best_vector = seed_vector.copy()
    np.save(run_dir / "best.npy", best_vector)
    # Training-only: validation/baseline above and the final audit always use a plain
    # full board, so every reported number stays comparable across runs.
    curriculum = (args.curriculum_clear_max, args.curriculum_prob)
    curriculum_label = (
        f"{args.curriculum_clear_max:g}@p{args.curriculum_prob:g}"
        if args.curriculum_clear_max > 0
        else "off"
    )
    print(
        f"Evo run: {run_dir} | seed={seed_label} | pop={config.population} "
        f"| gens={args.generations} | workers={args.workers} | curriculum={curriculum_label} "
        f"| val_rotate={args.val_rotate_every or 'off'}\n"
        f"Baseline seed validation score ({args.val_seeds} plain-board seeds): {best_val:.1f}"
    )
    if args.curriculum_clear_max > 0 and args.curriculum_prob >= 0.95:
        print(
            "  WARNING: --curriculum-prob >= 0.95 leaves almost no full-board openings. "
            "Past runs died this way; ~0.5 is the recommended mix."
        )

    pool = None
    if args.workers > 1:
        import multiprocessing as mp

        pool = mp.Pool(processes=args.workers)
    try:
        for generation in range(args.generations):
            started = time.monotonic()
            sigma = config.sigma_at(generation)
            # Rotate the holdout before scoring this generation, re-measuring both the
            # incumbent best and the baseline on the fresh boards so every comparison
            # stays within one seed set. all_time_best may legitimately drop here.
            if args.val_rotate_every and generation // args.val_rotate_every != val_rotation:
                val_rotation = generation // args.val_rotate_every
                val_seeds = validation_seeds(args.seed, args.val_seeds, val_rotation)
                previous_best = best_val
                best_val, _ = genome_fitness(best_vector, val_seeds)
                baseline_val, _ = genome_fitness(seed_vector, val_seeds)
                print(
                    f"  validation seeds rotated (#{val_rotation}): best re-scored "
                    f"{previous_best:.1f} -> {best_val:.1f}, baseline {baseline_val:.1f}"
                )
            train_seeds = generation_seeds(args.seed, generation, args.eval_seeds)
            fitnesses = evaluate_population(
                population, train_seeds, workers=args.workers, pool=pool,
                curriculum=curriculum,
            )
            order = np.argsort(fitnesses)[::-1]
            gen_best_vector = population[int(order[0])]
            val_score, _ = genome_fitness(gen_best_vector, val_seeds)

            improved = val_score > best_val
            if improved:
                best_val = val_score
                best_vector = gen_best_vector.copy()
                np.save(run_dir / "best.npy", best_vector)

            history.append(
                {
                    "gen": generation,
                    "best": round(float(fitnesses.max()), 1),
                    "mean": round(float(fitnesses.mean()), 1),
                    "val": round(float(val_score), 1),
                }
            )
            # Publish the live-viewer state on a time budget (or on any improvement /
            # the final gen) so the 6-brain arena and chart stay fresh without churning
            # disk every generation when generations are sub-second.
            now = time.monotonic()
            is_last = generation == args.generations - 1
            published = True
            if args.live_export and (
                improved or is_last or now - last_publish >= args.viewer_interval
            ):
                published = publish_viewer_state(
                    population, fitnesses, order, generation, sigma,
                    best_val, baseline_val, history,
                )
                if published:
                    last_publish = now

            seconds = time.monotonic() - started
            with log_path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    (
                        generation,
                        f"{sigma:.5f}",
                        f"{float(fitnesses.max()):.1f}",
                        f"{float(fitnesses.mean()):.1f}",
                        f"{float(fitnesses.min()):.1f}",
                        f"{val_score:.1f}",
                        f"{best_val:.1f}",
                        f"{seconds:.1f}",
                        val_rotation,
                    )
                )
            print(
                f"gen {generation:4d} | sigma {sigma:.4f} | train best "
                f"{float(fitnesses.max()):6.1f} mean {float(fitnesses.mean()):6.1f} "
                f"| val {val_score:6.1f} | all-time {best_val:6.1f}"
                f"{'  <-- new best' if improved else ''} | {seconds:4.1f}s"
                f"{'' if published else '  (viewer frame dropped)'}"
            )

            population, _ = next_generation(population, fitnesses, config, sigma, rng)
    except KeyboardInterrupt:
        print("\nInterrupted — keeping best genome saved so far.")
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    print(f"\nBest genome saved to {run_dir / 'best.npy'} (validation {best_val:.1f})")

    audit_payload = None
    if args.audit_episodes and not args.from_scratch:
        print(f"Auditing best vs champion on {args.audit_episodes} paired seeds...")
        champion_vector = g.load_champion(args.champion)
        gate = paired_audit(best_vector, champion_vector, args.audit_episodes, args.audit_seed)
        verdict = "PROMOTE" if gate["accepted"] else "KEEP CHAMPION"
        audit_payload = {
            "accepted": bool(gate["accepted"]),
            "candidate_mean": float(gate["candidate_mean"]),
            "incumbent_mean": float(gate["incumbent_mean"]),
            "mean_difference": float(gate["mean_difference"]),
            "lower_confidence": float(gate["lower_confidence"]),
            "episodes": args.audit_episodes,
            "seed_base": args.audit_seed + AUDIT_SEED_OFFSET,
            "candidate": str(run_dir / "best.npy"),
            "incumbent": seed_label,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "sort_key": f"{run_dir.name}-999999",
        }
        _atomic_write_json(run_dir / "audit.json", audit_payload)
        print(
            f"Candidate {float(gate['candidate_mean']):.2f} vs champion "
            f"{float(gate['incumbent_mean']):.2f} | diff {float(gate['mean_difference']):+.2f} "
            f"(lower-95 {float(gate['lower_confidence']):+.2f}, bar >10) | {verdict}"
        )
        if gate["accepted"]:
            print(
                "To deploy: export best.npy to rl/policy/ and re-audit with rl.audit "
                "(see HANDOFF). Not done automatically."
            )

    run_metadata.update(
        {
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "last_generation": history[-1]["gen"] if history else None,
            "best_validation": float(best_val),
            "audit_recorded": audit_payload is not None,
        }
    )
    _atomic_write_json(run_dir / "run.json", run_metadata)


if __name__ == "__main__":
    main()
