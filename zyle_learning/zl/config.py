"""Shared defaults for observations, training, and generalization evaluation."""

from __future__ import annotations

from dataclasses import dataclass

OBSERVATION_SIZE = 96
BASE_CHANNELS = 8
FRAME_STACK = 4
OBSERVATION_CHANNELS = BASE_CHANNELS * FRAME_STACK
LEGACY_BASE_CHANNELS = 7
LEGACY_OBSERVATION_CHANNELS = LEGACY_BASE_CHANNELS * FRAME_STACK

# Level 4 is deliberately absent from the default training distribution. Its 48x24
# pixel-art wall is the strongest built-in test of scale and shape generalization.
HELD_OUT_LEVEL = 4
TRAIN_BUILTIN_LEVELS = (1, 2, 3)

TRAIN_SEED = 17_071
EVAL_BUILTIN_SEEDS = (41_003, 41_009, 41_021, 41_027, 41_033)
EVAL_PROCEDURAL_SEEDS = tuple(range(91_000, 91_050))
# Used for checkpoint selection during mixed training. These are intentionally
# disjoint from the sealed M3 procedural test seeds above.
VALIDATION_PROCEDURAL_SEEDS = tuple(range(81_000, 81_010))


@dataclass(frozen=True)
class TrainDefaults:
    total_timesteps: int = 2_000_000
    n_envs: int = 8
    n_steps: int = 256
    batch_size: int = 256
    learning_rate: float = 2.5e-4
    gamma: float = 0.995
    gae_lambda: float = 0.95
    ent_coef: float = 0.01
    clip_range: float = 0.10
    target_kl: float = 0.03
    n_epochs: int = 4
    checkpoint_freq: int = 250_000
    procedural_probability: float = 0.75


TRAIN_DEFAULTS = TrainDefaults()
