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
from typing import NamedTuple

import numpy as np

from rl.breakout_env import MAX_EPISODE_STEPS, BreakoutEnv
from rl.evo import genome as g

class Curriculum(NamedTuple):
    """Training-only board pre-clearing, so genomes practise the fast late game they
    otherwise almost never reach.

    ``clear_min``/``clear_max`` bound the pre-cleared fraction; narrowing the band
    concentrates practice on the endgame instead of spending most episodes on states
    the agent already handles. Field order keeps plain ``(clear_max, prob)`` tuples
    valid, so older call sites and pickled payloads still work.
    """

    clear_max: float = 0.0
    prob: float = 1.0
    clear_min: float = 0.0


NO_CURRICULUM = Curriculum()


def rollout_score(
    arrays: dict[str, np.ndarray],
    seed: int,
    max_steps: int,
    curriculum: Curriculum = NO_CURRICULUM,
    aim_threshold: int = 0,
) -> tuple[float, int, float, float]:
    """Play one greedy single-life episode; return (score, steps, destroyed_fraction).

    ``destroyed_fraction`` is 1.0 only when the whole wall is gone, which is what the
    finishing bonus keys off — raw score alone treats the brutal last brick exactly
    like the trivial first one.

    The curriculum draw comes from the env's seeded RNG, so identical seeds give
    identical pre-cleared boards across genomes — the comparison stays fair.
    """
    # Normalises plain tuples too, so a 2-tuple (clear_max, prob) still works.
    settings = Curriculum(*curriculum)
    env = BreakoutEnv(
        curriculum_clear_max=settings.clear_max,
        curriculum_prob=settings.prob,
        curriculum_clear_min=settings.clear_min,
        aim_threshold=aim_threshold,
    )
    observation, info = env.reset(seed=seed)
    done = False
    steps = 0
    while not done:
        observation, _, terminated, truncated, info = env.step(g.act(arrays, observation))
        done = terminated or truncated
        steps += 1
        if steps >= max_steps:
            break
    total = len(env.bricks) or 1
    destroyed_fraction = (total - env.bricks_alive) / total
    return float(info["score"]), steps, float(destroyed_fraction), env.aim_score


class FitnessResult(NamedTuple):
    """``mean`` is the selection value (shaped when a finishing bonus is set);
    ``scores`` is always the RAW per-episode score, so validation and audits stay
    comparable; ``clear_rate`` is the fraction of episodes that cleared the wall."""

    mean: float
    scores: np.ndarray
    clear_rate: float


def finishing_bonus(destroyed_fraction: float, clear_bonus: float, clear_power: float) -> float:
    """Reward that ramps steeply as the wall empties, peaking on a full clear.

    Raw score is essentially linear in bricks destroyed, so the last (hardest) brick
    pays the same as the first (trivial) one — which is why nothing we tried ever
    selected for *finishing*. Raising the destroyed fraction to a power concentrates
    the reward at the end: at power 4, clearing 2/3 of the wall is worth only ~20% of
    the bonus, 90% is worth ~66%, and a full clear pays it all.
    """
    if clear_bonus <= 0.0:
        return 0.0
    return clear_bonus * max(0.0, min(1.0, destroyed_fraction)) ** clear_power


def genome_fitness(
    vector: np.ndarray,
    seeds: tuple[int, ...],
    max_steps: int = MAX_EPISODE_STEPS,
    curriculum: Curriculum = NO_CURRICULUM,
    clear_bonus: float = 0.0,
    clear_power: float = 4.0,
    aim_bonus: float = 0.0,
    aim_threshold: int = 0,
) -> FitnessResult:
    """Fitness over ``seeds`` for one genome (unflattened once, reused).

    With ``clear_bonus`` at its default 0 this is exactly the old mean raw score.
    """
    arrays = g.unflatten(vector)
    raw: list[float] = []
    shaped: list[float] = []
    clears = 0
    for seed in seeds:
        score, _, destroyed, aim = rollout_score(
            arrays, seed, max_steps, curriculum, aim_threshold
        )
        raw.append(score)
        shaped.append(
            score + finishing_bonus(destroyed, clear_bonus, clear_power) + aim_bonus * aim
        )
        clears += destroyed >= 1.0
    scores = np.array(raw, dtype=np.float64)
    return FitnessResult(
        mean=float(np.mean(shaped)),
        scores=scores,
        clear_rate=clears / len(seeds) if seeds else 0.0,
    )


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


def _fitness_worker(payload: tuple) -> float:
    # Trailing elements are optional so shorter (older) payload tuples stay valid.
    vector, seeds, max_steps = payload[0], payload[1], payload[2]
    curriculum = payload[3] if len(payload) > 3 else NO_CURRICULUM
    clear_bonus = payload[4] if len(payload) > 4 else 0.0
    clear_power = payload[5] if len(payload) > 5 else 4.0
    aim_bonus = payload[6] if len(payload) > 6 else 0.0
    aim_threshold = payload[7] if len(payload) > 7 else 0
    return genome_fitness(
        vector, seeds, max_steps, curriculum, clear_bonus, clear_power, aim_bonus, aim_threshold
    ).mean


def evaluate_population(
    vectors: list[np.ndarray],
    seeds: tuple[int, ...],
    *,
    workers: int = 1,
    max_steps: int = MAX_EPISODE_STEPS,
    pool: "mp.pool.Pool | None" = None,
    curriculum: Curriculum = NO_CURRICULUM,
    clear_bonus: float = 0.0,
    clear_power: float = 4.0,
    aim_bonus: float = 0.0,
    aim_threshold: int = 0,
) -> np.ndarray:
    """Return a float64 fitness array aligned with ``vectors``.

    Serial when ``workers <= 1`` (also the test path); otherwise a caller-provided
    ``pool`` is reused across generations, or a transient one is created.
    """
    payloads = [
        (vector, seeds, max_steps, curriculum, clear_bonus, clear_power, aim_bonus, aim_threshold)
        for vector in vectors
    ]
    if workers <= 1 and pool is None:
        return np.array([_fitness_worker(p) for p in payloads], dtype=np.float64)
    if pool is not None:
        return np.array(pool.map(_fitness_worker, payloads), dtype=np.float64)
    with mp.Pool(processes=workers) as owned_pool:
        return np.array(owned_pool.map(_fitness_worker, payloads), dtype=np.float64)
