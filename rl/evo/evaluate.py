"""Fitness evaluation for evolved genomes.

Fitness is the mean *raw game score* over a fixed set of seeds, using the plain
headless `BreakoutEnv` (no curriculum, no reward shaping) — identical to what the
trainer's greedy eval and the browser arena measure. Sharing one seed set across
the whole population within a generation makes the comparison fair (common random
numbers); varying it across generations avoids overfitting to particular ball
starts.

Evaluation is embarrassingly parallel: each genome's rollouts are independent, so
a `multiprocessing.Pool` fans them across CPU cores. The env and forward pass are
pure numpy, so there is no GPU contention and no torch import.
"""

from __future__ import annotations

import multiprocessing as mp

import numpy as np

from rl.breakout_env import MAX_EPISODE_STEPS, BreakoutEnv
from rl.evo import genome as g

# (curriculum_clear_max, curriculum_prob). Pre-clears a random fraction of the wall
# at reset and advances ball speed to match, so genomes actually practise the fast
# mid/late game they otherwise almost never reach. NO_CURRICULUM is a plain board.
Curriculum = tuple[float, float]
NO_CURRICULUM: Curriculum = (0.0, 1.0)


def rollout_score(
    arrays: dict[str, np.ndarray],
    seed: int,
    max_steps: int,
    curriculum: Curriculum = NO_CURRICULUM,
) -> tuple[float, int]:
    """Play one greedy single-life episode; return (score, steps).

    The curriculum draw comes from the env's seeded RNG, so identical seeds give
    identical pre-cleared boards across genomes — the comparison stays fair.
    """
    clear_max, prob = curriculum
    env = BreakoutEnv(curriculum_clear_max=clear_max, curriculum_prob=prob)
    observation, info = env.reset(seed=seed)
    done = False
    steps = 0
    while not done:
        observation, _, terminated, truncated, info = env.step(g.act(arrays, observation))
        done = terminated or truncated
        steps += 1
        if steps >= max_steps:
            break
    return float(info["score"]), steps


def genome_fitness(
    vector: np.ndarray,
    seeds: tuple[int, ...],
    max_steps: int = MAX_EPISODE_STEPS,
    curriculum: Curriculum = NO_CURRICULUM,
) -> tuple[float, np.ndarray]:
    """Mean score over ``seeds`` for one genome (unflattened once, reused)."""
    arrays = g.unflatten(vector)
    scores = np.array(
        [rollout_score(arrays, seed, max_steps, curriculum)[0] for seed in seeds],
        dtype=np.float64,
    )
    return float(scores.mean()), scores


# --- Parallel population evaluation ------------------------------------------
# Module-level so multiprocessing (spawn on Windows) can pickle the target. The
# worker receives a plain tuple; numpy arrays pickle fine.

def _episode_worker(payload: tuple[np.ndarray, int, int, Curriculum]) -> float:
    vector, seed, max_steps, curriculum = payload
    return rollout_score(g.unflatten(vector), seed, max_steps, curriculum)[0]


def score_episodes(
    vector: np.ndarray,
    seeds: "tuple[int, ...] | list[int]",
    *,
    workers: int = 1,
    max_steps: int = MAX_EPISODE_STEPS,
    curriculum: Curriculum = NO_CURRICULUM,
) -> np.ndarray:
    """Per-episode scores for ONE genome across ``seeds``, parallel across cores.

    Used by the paired audit: scoring both policies on identical seeds gives the
    common-random-numbers comparison the promotion gate expects.
    """
    payloads = [(vector, int(seed), max_steps, curriculum) for seed in seeds]
    if workers <= 1:
        return np.array([_episode_worker(p) for p in payloads], dtype=np.float64)
    with mp.Pool(processes=workers) as pool:
        return np.array(pool.map(_episode_worker, payloads), dtype=np.float64)


def _fitness_worker(
    payload: tuple[np.ndarray, tuple[int, ...], int] | tuple[np.ndarray, tuple[int, ...], int, Curriculum]
) -> float:
    # Accepts the 3-tuple (no curriculum) and 4-tuple forms so existing callers
    # and pickled payloads stay valid.
    vector, seeds, max_steps = payload[0], payload[1], payload[2]
    curriculum = payload[3] if len(payload) > 3 else NO_CURRICULUM
    return genome_fitness(vector, seeds, max_steps, curriculum)[0]


def evaluate_population(
    vectors: list[np.ndarray],
    seeds: tuple[int, ...],
    *,
    workers: int = 1,
    max_steps: int = MAX_EPISODE_STEPS,
    pool: "mp.pool.Pool | None" = None,
    curriculum: Curriculum = NO_CURRICULUM,
) -> np.ndarray:
    """Return a float64 fitness array aligned with ``vectors``.

    Serial when ``workers <= 1`` (also the test path); otherwise a caller-provided
    ``pool`` is reused across generations, or a transient one is created.
    """
    payloads = [(vector, seeds, max_steps, curriculum) for vector in vectors]
    if workers <= 1 and pool is None:
        return np.array([_fitness_worker(p) for p in payloads], dtype=np.float64)
    if pool is not None:
        return np.array(pool.map(_fitness_worker, payloads), dtype=np.float64)
    with mp.Pool(processes=workers) as owned_pool:
        return np.array(owned_pool.map(_fitness_worker, payloads), dtype=np.float64)
