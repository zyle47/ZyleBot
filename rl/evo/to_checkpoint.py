"""Convert an evolved genome into a torch checkpoint that ``rl.train`` can resume.

Bridges the two optimizers: evolution searches weight-space directly and saves a
flat numpy genome, while the DQN trainer resumes from a torch checkpoint holding
online/target/optimizer state. This writes the latter from the former, so gradient
fine-tuning can pick up exactly where evolution left off::

    rl\\venv\\Scripts\\python.exe -m rl.evo.to_checkpoint ^
        --genome rl\\runs_evo\\<run>\\best.npy --output rl\\runs_evo\\<run>\\evolved.pt

    rl\\venv\\Scripts\\python.exe -m rl.train --resume rl\\runs_evo\\<run>\\evolved.pt ^
        --fork-run --reset-optimizer --learning-rate 1e-5 --learn-every 4 ^
        --anchor-steps 50000 --anchor-fraction 0.25 --n-step 3 --priority-alpha 0.6

**Caveat worth knowing:** evolution optimised raw score, so the network's outputs
are action *preferences*, not calibrated Q-values. TD updates may spend a while
re-scaling them and can degrade the evolved behaviour — hence the low learning
rate, the frozen-champion anchor, and the paired promotion gate. The gate means
the worst case is wasted compute, never a lost champion.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from rl.dqn.agent import DQNAgent
from rl.evo import genome as g
from rl.export_policy import STATE_KEYS


def genome_to_state_dict(arrays: dict[str, np.ndarray]) -> dict[str, torch.Tensor]:
    """Map the six named arrays onto the QNetwork's ``layers.{0,2,4}`` parameters."""
    return {
        torch_key: torch.tensor(np.asarray(arrays[name], dtype=np.float32))
        for name, torch_key in STATE_KEYS.items()
    }


def agent_from_genome(vector: np.ndarray, device: torch.device, steps: int) -> DQNAgent:
    """Build a DQNAgent whose online AND target networks are the evolved brain."""
    agent = DQNAgent(device)
    state = genome_to_state_dict(g.unflatten(vector))
    agent.online.load_state_dict(state)
    agent.target.load_state_dict(state)
    agent.agent_steps = int(steps)
    return agent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genome", type=Path, required=True, help=".npy genome or .npz policy")
    parser.add_argument("--output", type=Path, required=True, help="checkpoint path to write")
    parser.add_argument(
        "--steps",
        type=int,
        default=3_050_000,
        help="agent_steps to record; defaults to the champion lineage so epsilon stays annealed",
    )
    parser.add_argument(
        "--eval-score",
        type=float,
        default=0.0,
        help="best_eval_score to record; a fork re-baselines this anyway",
    )
    args = parser.parse_args()

    from rl.train import save_checkpoint  # lazy: pulls the trainer's checkpoint schema

    vector = g.load_genome(args.genome)
    agent = agent_from_genome(vector, torch.device("cpu"), args.steps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_checkpoint(args.output, agent, args.eval_score, args.output.parent, eval_episodes=5)
    print(
        f"Wrote {args.output} from {args.genome} "
        f"(agent_steps={agent.agent_steps}, epsilon={agent.epsilon:.3f})\n"
        "Resume it with rl.train --resume <that path> --fork-run (see module docstring)."
    )


if __name__ == "__main__":
    main()
