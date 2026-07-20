"""Atomic export of torch checkpoint weights for the app's pure-numpy policy.

The write path is deliberately torch-free so it can be imported by any consumer
(CLI, `rl.train --live-export`, or a test) without pulling in the training stack.
Only `main()` touches torch, and it imports it lazily.

Publishing is atomic: the app may read the policy while an export is in flight,
so a partially written artifact must never be visible. The `.npz` is replaced
LAST — its replacement is the commit marker — and it also carries the
authoritative metadata scalars so the app never pairs fresh weights with a stale
`meta.json` during the two-file replacement window.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np

DEFAULT_OUTPUT = Path("rl/policy/breakout_policy.npz")
OBSERVATION_VERSION = "level1-v1"

# app-side numpy array name -> torch state_dict key.
STATE_KEYS = {
    "layer1_weight": "layers.0.weight",
    "layer1_bias": "layers.0.bias",
    "layer2_weight": "layers.2.weight",
    "layer2_bias": "layers.2.bias",
    "output_weight": "layers.4.weight",
    "output_bias": "layers.4.bias",
}


def arrays_from_online_state(state) -> dict[str, np.ndarray]:
    """Extract the six weight/bias arrays from a torch `online` state_dict.

    Kept torch-free at the type level: it only calls tensor methods on whatever
    is passed in, so importing this module never imports torch.
    """
    return {
        name: np.asarray(state[key].detach().cpu().numpy(), dtype=np.float32)
        for name, key in STATE_KEYS.items()
    }


def publish_policy(
    arrays: dict[str, np.ndarray],
    *,
    training_steps: int,
    eval_score: float | None,
    observation_version: str = OBSERVATION_VERSION,
    output: Path = DEFAULT_OUTPUT,
) -> dict:
    """Atomically publish the six weight arrays plus metadata to `output`.

    Order (per the arena brief): stage the NPZ, stage `meta.json`, `os.replace`
    `meta.json`, then `os.replace` the NPZ last as the commit marker. Abandoned
    temp files are cleaned up on any failure without touching the last good pair.
    Returns the human-readable metadata dict that was written.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    meta_path = output.with_name("meta.json")

    missing = [name for name in STATE_KEYS if name not in arrays]
    if missing:
        raise ValueError(f"missing weight arrays: {missing}")

    eval_value: float | None = None
    if eval_score is not None and math.isfinite(float(eval_score)):
        eval_value = float(eval_score)
    steps = int(training_steps)

    # Authoritative metadata also lives INSIDE the NPZ as scalar arrays, so a
    # reader that sees the freshly committed weights never has to consult a
    # possibly-stale meta.json. meta.json stays strict JSON (no NaN/Infinity).
    npz_payload: dict[str, np.ndarray] = {
        name: np.asarray(arrays[name], dtype=np.float32) for name in STATE_KEYS
    }
    npz_payload["observation_version"] = np.array(observation_version)
    npz_payload["training_steps"] = np.array(steps, dtype=np.int64)
    npz_payload["eval_score"] = np.array(
        np.nan if eval_value is None else eval_value, dtype=np.float64
    )
    meta = {
        "observation_version": observation_version,
        "training_steps": steps,
        "eval_score": eval_value,
    }

    tmp_npz: Path | None = None
    tmp_meta: Path | None = None
    try:
        # 1-2. Stage the NPZ in the destination directory (same filesystem), then
        # flush + fsync + close it completely before it can be referenced.
        fd, tmp_npz_name = tempfile.mkstemp(dir=output.parent, prefix=".tmp-policy-", suffix=".npz")
        os.close(fd)
        tmp_npz = Path(tmp_npz_name)
        with open(tmp_npz, "wb") as handle:
            np.savez(handle, **npz_payload)
            handle.flush()
            os.fsync(handle.fileno())

        # 3. Stage meta.json and close it.
        fd, tmp_meta_name = tempfile.mkstemp(dir=output.parent, prefix=".tmp-meta-", suffix=".json")
        os.close(fd)
        tmp_meta = Path(tmp_meta_name)
        with open(tmp_meta, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        # 4. Atomically replace meta.json.
        os.replace(tmp_meta, meta_path)
        tmp_meta = None
        # 5. Atomically replace the NPZ LAST — this is the commit marker.
        os.replace(tmp_npz, output)
        tmp_npz = None
    finally:
        # 6. Clean up any abandoned temp file without touching the last good export.
        for leftover in (tmp_npz, tmp_meta):
            if leftover is not None:
                try:
                    leftover.unlink()
                except OSError:
                    pass

    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--eval-score", type=float)
    args = parser.parse_args()

    import torch  # lazy: keeps this module importable without the training stack

    checkpoint = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    arrays = arrays_from_online_state(checkpoint["online"])
    eval_score = args.eval_score
    if eval_score is None:
        eval_score = checkpoint.get("best_eval_score")
    meta = publish_policy(
        arrays,
        training_steps=int(checkpoint.get("agent_steps", 0)),
        eval_score=eval_score,
        output=args.output,
    )
    print(
        f"Exported {args.output} and {args.output.with_name('meta.json')} "
        f"(steps={meta['training_steps']}, eval={meta['eval_score']})"
    )


if __name__ == "__main__":
    main()
