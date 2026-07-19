"""Export torch checkpoint weights for the app's pure-numpy policy."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import torch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("rl/policy/breakout_policy.npz"))
    parser.add_argument("--eval-score", type=float)
    args = parser.parse_args()

    checkpoint = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    state = checkpoint["online"]
    arrays = {
        "layer1_weight": state["layers.0.weight"].numpy(),
        "layer1_bias": state["layers.0.bias"].numpy(),
        "layer2_weight": state["layers.2.weight"].numpy(),
        "layer2_bias": state["layers.2.bias"].numpy(),
        "output_weight": state["layers.4.weight"].numpy(),
        "output_bias": state["layers.4.bias"].numpy(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.output, **arrays)
    eval_score = args.eval_score
    if eval_score is None:
        eval_score = checkpoint.get("best_eval_score")
    if eval_score is not None and not math.isfinite(float(eval_score)):
        eval_score = None
    meta = {
        "observation_version": "level1-v1",
        "training_steps": int(checkpoint.get("agent_steps", 0)),
        "eval_score": None if eval_score is None else float(eval_score),
    }
    args.output.with_name("meta.json").write_text(
        json.dumps(meta, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"Exported {args.output} and {args.output.with_name('meta.json')}")


if __name__ == "__main__":
    main()
