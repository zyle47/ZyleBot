from __future__ import annotations

import numpy as np

from zl.env.levels import builtin_level, cell_histogram, procedural_level


def test_level_one_exact_shape_and_points_encoding() -> None:
    level = builtin_level(1, np.random.default_rng(1))
    assert (level.cols, level.rows) == (10, 6)
    assert cell_histogram(level) == {"1": 60}


def test_builtin_dimensions_and_mechanics() -> None:
    level2 = builtin_level(2, np.random.default_rng(7))
    level3 = builtin_level(3, np.random.default_rng(7))
    level4 = builtin_level(4, np.random.default_rng(7))
    assert (level2.cols, level2.rows) == (16, 9)
    assert cell_histogram(level2) == {"1": 24, "2": 28, "3": 8, "4": 1, "5": 1}
    assert (level3.cols, level3.rows) == (20, 12)
    assert cell_histogram(level3) == {"2": 27, "3": 30, "4": 1, "5": 1}
    assert (level4.cols, level4.rows) == (48, 24)
    assert cell_histogram(level4) == {"art": 1152}


def test_level_two_random_splitter_is_seeded() -> None:
    a = builtin_level(2, np.random.default_rng(881))
    b = builtin_level(2, np.random.default_rng(881))
    c = builtin_level(2, np.random.default_rng(882))
    assert a.pattern == b.pattern
    assert a.pattern != c.pattern


def test_procedural_generator_is_seeded_and_covers_all_families() -> None:
    for family in ("scatter", "symmetric", "blobs", "strokes", "dense"):
        a = procedural_level(np.random.default_rng(1234), family=family)
        b = procedural_level(np.random.default_rng(1234), family=family)
        assert a == b
        assert a.family == family
        assert 10 <= a.cols <= 48
        assert 6 <= a.rows <= 24
        assert sum(cell != "0" for row in a.pattern for cell in row) >= 12
        histogram = cell_histogram(a)
        assert histogram.get("4", 0) <= 1
        assert histogram.get("5", 0) <= 1


def test_dense_family_covers_near_solid_large_boards() -> None:
    boards = [
        procedural_level(np.random.default_rng(seed), family="dense")
        for seed in range(200)
    ]
    assert any(level.cols >= 40 and level.rows >= 20 for level in boards)
    assert any(
        sum(cell != "0" for row in level.pattern for cell in row) == level.cols * level.rows
        for level in boards
    )
    assert any(
        level.cols == 48
        and level.rows == 24
        and sum(cell != "0" for row in level.pattern for cell in row) == 48 * 24
        for level in boards
    )


def test_fixed_eval_seeds_produce_distinct_unseen_boards() -> None:
    boards = {
        procedural_level(np.random.default_rng(seed)).pattern
        for seed in range(91_000, 91_020)
    }
    assert len(boards) == 20
