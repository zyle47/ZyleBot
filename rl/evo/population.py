"""Genetic operators: initialization, selection, crossover, mutation.

Classic, robust GA rather than anything exotic:

* **Elitism** copies the top-E genomes unchanged into the next generation. Seeded
  from the champion, this guarantees the best-so-far can never regress *on the
  shared evaluation seeds* — the champion is always in the gene pool.
* **Tournament selection** picks parents with tunable pressure.
* **Uniform crossover** mixes two parents gene-by-gene.
* **Gaussian mutation** perturbs genes by ``sigma``; ``sigma`` anneals across
  generations so early search is broad and late search refines.

All randomness comes from a passed-in ``numpy.random.Generator`` for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rl.evo.genome import GENOME_SIZE


@dataclass(frozen=True)
class EvoConfig:
    """Static hyperparameters for one evolution run."""

    population: int = 60
    elite: int = 3
    tournament: int = 3
    crossover_rate: float = 0.5
    mutation_rate: float = 1.0  # fraction of genes perturbed each mutation
    sigma_init: float = 0.02
    sigma_decay: float = 0.995  # per-generation multiplier
    sigma_min: float = 0.002

    def sigma_at(self, generation: int) -> float:
        return max(self.sigma_min, self.sigma_init * (self.sigma_decay ** generation))


def init_population(
    seed_vector: np.ndarray,
    size: int,
    sigma: float,
    rng: np.random.Generator,
) -> list[np.ndarray]:
    """Generation 0: the seed genome verbatim (index 0) plus mutated copies.

    Keeping the unmutated seed as individual 0 means the very first generation
    already contains the champion, so evolution starts from a known-good point.
    """
    seed_vector = np.asarray(seed_vector, dtype=np.float32)
    population = [seed_vector.copy()]
    for _ in range(size - 1):
        noise = rng.normal(0.0, sigma, GENOME_SIZE).astype(np.float32)
        population.append((seed_vector + noise).astype(np.float32))
    return population


def tournament_select(fitnesses: np.ndarray, k: int, rng: np.random.Generator) -> int:
    """Return the index of the fittest of ``k`` random contenders."""
    contenders = rng.integers(0, len(fitnesses), size=k)
    return int(contenders[int(np.argmax(fitnesses[contenders]))])


def crossover(a: np.ndarray, b: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Uniform per-gene crossover of two parents."""
    mask = rng.random(GENOME_SIZE) < 0.5
    return np.where(mask, a, b).astype(np.float32)


def mutate(
    vector: np.ndarray,
    sigma: float,
    mutation_rate: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Add Gaussian noise (std ``sigma``) to all genes, or a random fraction of them."""
    if sigma <= 0.0:
        return vector.astype(np.float32, copy=True)
    noise = rng.normal(0.0, sigma, GENOME_SIZE).astype(np.float32)
    if mutation_rate < 1.0:
        noise *= (rng.random(GENOME_SIZE) < mutation_rate)
    return (vector + noise).astype(np.float32)


def next_generation(
    vectors: list[np.ndarray],
    fitnesses: np.ndarray,
    config: EvoConfig,
    sigma: float,
    rng: np.random.Generator,
) -> tuple[list[np.ndarray], np.ndarray]:
    """Build the next generation and return it with the descending fitness order.

    Elites are carried over unchanged; the rest are bred by tournament selection,
    optional crossover, then mutation.
    """
    order = np.argsort(fitnesses)[::-1]
    children: list[np.ndarray] = [vectors[int(i)].copy() for i in order[: config.elite]]
    while len(children) < config.population:
        parent = vectors[tournament_select(fitnesses, config.tournament, rng)]
        if rng.random() < config.crossover_rate:
            mate = vectors[tournament_select(fitnesses, config.tournament, rng)]
            child = crossover(parent, mate, rng)
        else:
            child = parent.astype(np.float32, copy=True)
        children.append(mutate(child, sigma, config.mutation_rate, rng))
    return children, order
