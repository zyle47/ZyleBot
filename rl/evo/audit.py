"""Paired A/B audit of an evolved genome against the deployed champion.

The evolution counterpart to `rl/audit.py`: both policies play the **same** held-out
seeds (common random numbers), then the identical paired 95%-confidence gate the
trainer uses decides PROMOTE / KEEP. This is the only number that should ever
justify a deploy — a run's `all_time_best` is a max over many noisy generations and
reads optimistically high (1441.9 once audited out at 1128.8).

Audit whatever `best.npy` currently holds, without stopping the run::

    rl\\venv\\Scripts\\python.exe -m rl.evo.audit ^
        --candidate rl\\runs_evo\\<run>\\best.npy --workers 4

A PROMOTE verdict means the candidate's paired mean-difference lower-95 bound
cleared the same +10 bar as every DQN champion; only then publish it with
``rl.export_policy``-style deployment into ``rl/policy/``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from rl.evo import genome as g
from rl.evo.evaluate import score_episodes

DEFAULT_INCUMBENT = Path("rl/policy/breakout_policy.npz")
DEFAULT_SEED_BASE = 500_000  # held out from evolve.py's training/validation seed tags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=Path, required=True, help="genome under test (.npy or .npz)"
    )
    parser.add_argument(
        "--incumbent",
        type=Path,
        default=DEFAULT_INCUMBENT,
        help="policy to beat; defaults to the deployed champion",
    )
    parser.add_argument("--episodes", type=int, default=200, help="paired episodes (default 200)")
    parser.add_argument(
        "--seed-base",
        type=int,
        default=DEFAULT_SEED_BASE,
        help="first held-out episode seed; both policies use seed-base..+episodes",
    )
    parser.add_argument("--workers", type=int, default=4, help="parallel rollout processes")
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=10.0,
        help="lower-95 bound the mean difference must exceed to PROMOTE (default 10)",
    )
    args = parser.parse_args()
    if args.episodes < 2:
        parser.error("--episodes must be at least 2")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    from rl.train import paired_gate_decision  # lazy: keeps the rollout path torch-free

    seeds = [args.seed_base + i for i in range(args.episodes)]
    candidate = g.load_genome(args.candidate)
    incumbent = g.load_genome(args.incumbent)

    candidate_scores = score_episodes(candidate, seeds, workers=args.workers)
    incumbent_scores = score_episodes(incumbent, seeds, workers=args.workers)
    gate = paired_gate_decision(candidate_scores, incumbent_scores, args.min_improvement)
    verdict = "PROMOTE" if gate["accepted"] else "KEEP INCUMBENT"

    result = {
        "accepted": bool(gate["accepted"]),
        "candidate_mean": float(gate["candidate_mean"]),
        "incumbent_mean": float(gate["incumbent_mean"]),
        "mean_difference": float(gate["mean_difference"]),
        "lower_confidence": float(gate["lower_confidence"]),
        "episodes": args.episodes,
        "seed_base": args.seed_base,
        "candidate": str(args.candidate),
        "incumbent": str(args.incumbent),
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "sort_key": f"{args.candidate.parent.name}-999999",
    }
    audit_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    audit_path = (
        args.candidate.parent / f"audit-{audit_stamp}.json"
        if args.candidate.name == "best.npy"
        else args.candidate.with_suffix(f".{audit_stamp}.audit.json")
    )
    audit_path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    wins = int((candidate_scores > incumbent_scores).sum())
    print(f"Paired audit on {args.episodes} held-out seeds (base {args.seed_base})")
    print(f"  candidate : mean {float(gate['candidate_mean']):8.1f}   [{args.candidate}]")
    print(f"  incumbent : mean {float(gate['incumbent_mean']):8.1f}   [{args.incumbent}]")
    print(
        f"  difference: {float(gate['mean_difference']):+8.1f}   "
        f"(lower-95 {float(gate['lower_confidence']):+.1f}, bar >{args.min_improvement:g})"
    )
    print(f"  head-to-head: candidate wins {wins}/{args.episodes}")
    print(f"  VERDICT: {verdict}")
    print(f"  history record: {audit_path}")
    if gate["accepted"]:
        print("  -> Deployable. Ask before overwriting rl/policy/ (the live champion).")


if __name__ == "__main__":
    main()
