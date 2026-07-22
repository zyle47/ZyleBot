"""Genome codec + numpy forward pass for the evolved Breakout policy.

A genome is a flat ``float32`` vector holding the six weight/bias arrays of the
same 78->256->256->3 MLP the DQN uses (`rl/dqn/network.py`). The array names,
shapes and order match `rl.export_policy` exactly, so a genome round-trips into
the app's `.npz` policy with zero translation and the arena plays precisely the
weights that evolved.

Torch-free by design: this module imports only numpy, so the whole evolution loop
stays off the training stack and parallelizes cheaply across CPU cores.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# (name, shape) in the canonical order used by rl.export_policy / app.rl_policy.
# Keep this list, that module's STATE_KEYS, and _EXPECTED_SHAPES in agreement.
ARRAY_SPECS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("layer1_weight", (256, 78)),
    ("layer1_bias", (256,)),
    ("layer2_weight", (256, 256)),
    ("layer2_bias", (256,)),
    ("output_weight", (3, 256)),
    ("output_bias", (3,)),
)
ARRAY_NAMES: tuple[str, ...] = tuple(name for name, _ in ARRAY_SPECS)
_SIZES: tuple[int, ...] = tuple(int(np.prod(shape)) for _, shape in ARRAY_SPECS)
_OFFSETS: tuple[int, ...] = tuple(int(np.cumsum((0, *_SIZES))[i]) for i in range(len(_SIZES)))
GENOME_SIZE: int = int(sum(_SIZES))


def flatten(arrays: dict[str, np.ndarray]) -> np.ndarray:
    """Concatenate the six arrays (canonical order) into one float32 vector."""
    parts = []
    for name, shape in ARRAY_SPECS:
        array = np.asarray(arrays[name], dtype=np.float32)
        if array.shape != shape:
            raise ValueError(f"{name} has shape {array.shape}, expected {shape}")
        parts.append(array.reshape(-1))
    return np.concatenate(parts).astype(np.float32, copy=False)


def unflatten(vector: np.ndarray) -> dict[str, np.ndarray]:
    """Split a genome vector back into the six named weight/bias arrays."""
    vector = np.asarray(vector, dtype=np.float32)
    if vector.shape != (GENOME_SIZE,):
        raise ValueError(f"genome vector has shape {vector.shape}, expected {(GENOME_SIZE,)}")
    arrays: dict[str, np.ndarray] = {}
    for (name, shape), offset, size in zip(ARRAY_SPECS, _OFFSETS, _SIZES):
        end = offset + size
        arrays[name] = vector[offset:end].reshape(shape)
    return arrays


def load_champion(path: Path | str) -> np.ndarray:
    """Load the deployed policy `.npz` (the DQN champion) as a genome vector."""
    with np.load(Path(path), allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in ARRAY_NAMES}
    return flatten(arrays)


def load_genome(path: Path | str) -> np.ndarray:
    """Load either a raw ``.npy`` genome vector or a published policy ``.npz``."""
    path = Path(path)
    if path.suffix == ".npy":
        return np.asarray(np.load(path), dtype=np.float32)
    return load_champion(path)


def random_genome(rng: np.random.Generator, scale: float = 0.1) -> np.ndarray:
    """A from-scratch genome: small Gaussian weights. Far weaker than seeding the
    champion — provided only for the authentic evolve-from-zero experiment."""
    return rng.normal(0.0, scale, GENOME_SIZE).astype(np.float32)


def q_values(arrays: dict[str, np.ndarray], observation: np.ndarray) -> np.ndarray:
    """Two-hidden-layer ReLU MLP forward pass.

    Bit-for-bit the same computation as `app.rl_policy.NumpyPolicy.q_values`, so a
    genome's greedy action here equals the action the arena will take once the
    genome is exported.
    """
    x = np.asarray(observation, dtype=np.float32)
    x = np.maximum(0.0, x @ arrays["layer1_weight"].T + arrays["layer1_bias"])
    x = np.maximum(0.0, x @ arrays["layer2_weight"].T + arrays["layer2_bias"])
    return x @ arrays["output_weight"].T + arrays["output_bias"]


def act(arrays: dict[str, np.ndarray], observation: np.ndarray) -> int:
    """Greedy action (argmax over Q-values)."""
    return int(np.argmax(q_values(arrays, observation)))
