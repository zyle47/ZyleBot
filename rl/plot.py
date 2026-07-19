"""Plot evaluation score and training loss from a run CSV."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path("rl/runs/.matplotlib-cache").resolve()))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    with (args.run / "log.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    steps = [int(row["steps"]) for row in rows]
    scores = [float(row["eval_score_mean"]) for row in rows]
    losses = [float(row["loss"]) for row in rows]

    figure, (score_axis, loss_axis) = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
    score_axis.plot(steps, scores, color="#67ffb7")
    score_axis.set_ylabel("Eval score")
    score_axis.grid(alpha=0.25)
    loss_axis.plot(steps, losses, color="#ff3cac")
    loss_axis.set_xlabel("Agent steps")
    loss_axis.set_ylabel("Huber loss")
    loss_axis.grid(alpha=0.25)
    figure.tight_layout()
    output = args.run / "learning-curves.png"
    figure.savefig(output, dpi=150)
    print(output)


if __name__ == "__main__":
    main()
