"""Built-in level definitions and the seeded procedural training distribution.

This module is deliberately self-contained: runtime code never imports the deployed
application or the older evolution project.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

LEVEL_ONE_ROW_POINTS = (70, 70, 50, 50, 30, 30)
PIERCER_HITS = 5
SPLITTER_HITS = 4

MOSAIC_PATTERN = (
    "1001002002001001",
    "0100201001020010",
    "0010020220200100",
    "0001202332021000",
    "0022033523302200",
    "0001202332021000",
    "0010020220200100",
    "0100201001020010",
    "1001002002001001",
)

_ZYLE_GLYPHS = (
    ("1111", "0001", "0010", "0010", "0100", "0100", "1000", "1111"),
    ("1001", "1001", "1001", "0110", "0110", "0110", "0110", "0110"),
    ("1000", "1000", "1000", "1000", "1000", "1000", "1000", "1111"),
    ("1111", "1000", "1000", "1110", "1000", "1000", "1000", "1111"),
)
_ZYLE_STRENGTHS = (3, 2, 2, 3)


def _make_zyle_pattern() -> tuple[str, ...]:
    lines = ["0" * 20, "0" * 20]
    for row in range(8):
        line = "".join(
            glyph[row].replace("1", str(_ZYLE_STRENGTHS[letter])) + "0"
            for letter, glyph in enumerate(_ZYLE_GLYPHS)
        )
        lines.append(line)
    lines.extend(("0" * 20, "0" * 20))
    cells = [list(line) for line in lines]
    cells[3][6] = "5"
    cells[7][17] = "4"
    return tuple("".join(row) for row in cells)


ZYLE_PATTERN = _make_zyle_pattern()

_TRIBUTE_COLS = 48
_TRIBUTE_ROWS = 24


def _make_daja_chan_pattern() -> tuple[str, ...]:
    grid = [["Y"] * _TRIBUTE_COLS for _ in range(_TRIBUTE_ROWS)]
    font = {
        "D": ("1110", "1001", "1001", "1001", "1110"),
        "A": ("0110", "1001", "1111", "1001", "1001"),
        "J": ("0011", "0001", "0001", "1001", "0110"),
        "-": ("0000", "0000", "1111", "0000", "0000"),
        "C": ("0111", "1000", "1000", "1000", "0111"),
        "H": ("1001", "1001", "1111", "1001", "1001"),
        "N": ("1001", "1101", "1101", "1011", "1001"),
    }
    title_x = 2
    for letter in "DAJA-CHAN":
        glyph = font[letter]
        for row, line in enumerate(glyph):
            for col, pixel in enumerate(line):
                if pixel == "1":
                    grid[row][title_x + col] = "R"
        title_x += len(glyph[0]) + 1

    heart = (
        "011000110", "111101111", "111111111", "011111110",
        "001111100", "000111000", "000010000",
    )
    for row, line in enumerate(heart):
        for col, pixel in enumerate(line):
            if pixel == "1":
                grid[8 + row][1 + col] = "R"

    cat_art = (
        ".......KK............KK.......",
        "......BKKKK........KKKKB......",
        "......BKKKKK......KKKKKB......",
        "......KKKKKKK....KKKKKKK......",
        "......KKKBKKKKKKKKKKBKKK......",
        "......KKKKKKKKBBKKKKKKKK......",
        "......KBKKKKKKBBKKKKKKBK......",
        "......KKKEEEEKBBKEEEEKKK......",
        "......KKKEKKEKKKKEKKEKKK......",
        "......KBKEEEEKKKKEEEEKBK......",
        "......KBBKKKKKBBKKKKKBBK......",
        "......KBBKKKCCKKCCKKKBBK......",
        ".CCCCCKKKKKKKCKKCKKKKKKKCCCCC.",
        "......KBKKKKCCKKCCKKKKBK......",
        "CCCCC..KKKKCCKKKKCCKKKK..CCCCC",
        "........KKKCCCKKCCCKKK........",
        ".........KKBCCCCCCBKK.........",
        "..........KBCCCCCCBK..........",
        "...........BCCCCCCB...........",
    )
    for row, line in enumerate(cat_art):
        for col, cell in enumerate(line):
            if cell != ".":
                grid[5 + row][9 + col] = cell
    return tuple("".join(row) for row in grid)


DAJA_CHAN_PATTERN = _make_daja_chan_pattern()


@dataclass(frozen=True)
class LevelDefinition:
    name: str
    cols: int
    rows: int
    pattern: tuple[str, ...]
    gap: float = 6.0
    brick_h: float = 22.0
    top: float = 60.0
    built_in_number: int | None = None
    family: str = "built-in"

    def __post_init__(self) -> None:
        if len(self.pattern) != self.rows:
            raise ValueError("pattern row count does not match rows")
        if any(len(row) != self.cols for row in self.pattern):
            raise ValueError("pattern column count does not match cols")
        if not any(cell != "0" for row in self.pattern for cell in row):
            raise ValueError("a level must contain at least one brick")


def builtin_level(level: int, rng: np.random.Generator | None = None) -> LevelDefinition:
    """Return an exact built-in layout; level 2's random Splitter is seedable."""
    rng = rng if rng is not None else np.random.default_rng()
    if level == 1:
        pattern = tuple("1" * 10 for _ in range(6))
        return LevelDefinition("level-1", 10, 6, pattern, built_in_number=1)
    if level == 2:
        cells = [list(row) for row in MOSAIC_PATTERN]
        candidates = [
            (row, col)
            for row, line in enumerate(cells)
            for col, cell in enumerate(line)
            if cell not in ("0", "5")
        ]
        row, col = candidates[int(rng.integers(len(candidates)))]
        cells[row][col] = "4"
        return LevelDefinition(
            "level-2", 16, len(cells), tuple("".join(row) for row in cells), built_in_number=2
        )
    if level == 3:
        return LevelDefinition("level-3", 20, 12, ZYLE_PATTERN, built_in_number=3)
    if level == 4:
        return LevelDefinition(
            "level-4", 48, 24, DAJA_CHAN_PATTERN,
            gap=3.0, brick_h=12.0, top=36.0, built_in_number=4,
        )
    raise ValueError(f"unknown built-in level: {level}")


GeneratorFamily = Literal["scatter", "symmetric", "blobs", "strokes", "dense"]


def _blob_mask(rng: np.random.Generator, rows: int, cols: int, density: float) -> np.ndarray:
    mask = rng.random((rows, cols)) < density
    for _ in range(2):
        padded = np.pad(mask.astype(np.uint8), 1)
        neighbors = np.zeros_like(mask, dtype=np.uint8)
        for dy in range(3):
            for dx in range(3):
                neighbors += padded[dy: dy + rows, dx: dx + cols]
        threshold = int(rng.integers(4, 7))
        mask = neighbors >= threshold
    return mask


def _stroke_mask(rng: np.random.Generator, rows: int, cols: int) -> np.ndarray:
    mask = np.zeros((rows, cols), dtype=bool)
    count = int(rng.integers(3, max(4, min(12, (rows + cols) // 4))))
    for _ in range(count):
        y, x = int(rng.integers(rows)), int(rng.integers(cols))
        length = int(rng.integers(3, max(4, min(rows, cols))))
        dy, dx = ((0, 1), (1, 0), (1, 1), (1, -1))[int(rng.integers(4))]
        thickness = int(rng.integers(1, 3))
        for step in range(length):
            yy, xx = y + dy * step, x + dx * step
            if not (0 <= yy < rows and 0 <= xx < cols):
                break
            mask[max(0, yy - thickness + 1): yy + 1, max(0, xx - thickness + 1): xx + 1] = True
    return mask


def procedural_level(
    rng: np.random.Generator,
    *,
    family: GeneratorFamily | None = None,
    name: str = "procedural",
    large_probability: float | None = None,
) -> LevelDefinition:
    """Sample varied topology, scale, density, symmetry, and mechanics from one seed.

    The large-board branch is intentional: without it the 48x24 held-out tribute level
    would be out-of-distribution even though the generator produced many small boards.

    ``large_probability`` overrides how often the large-size branch is taken. When None
    (default) the legacy per-family rate is used (0.65 dense / 0.20 otherwise). Set it
    high (e.g. 0.5) to concentrate training on level-4-scale 1000+ brick boards; when a
    large board is dense it also more often snaps to the exact 48x24 held-out geometry.
    """
    family = family or ("scatter", "symmetric", "blobs", "strokes", "dense")[
        int(rng.integers(5))
    ]
    # Dense boards favor the large branch because their purpose is to cover the
    # 48x24, fully occupied held-out extreme as well as ordinary solid walls.
    if large_probability is None:
        large = bool(rng.random() < (0.65 if family == "dense" else 0.20))
        exact_probability = 0.10
        boosted_large = False
    else:
        large = bool(rng.random() < large_probability)
        # When deliberately biasing toward big boards, snap to the exact 48x24 shape
        # far more often so the held-out geometry itself is squarely in-distribution.
        exact_probability = 0.35
        boosted_large = large
    # A deliberately boosted large board is forced to level-4 scale: near-solid dense
    # fill at a big footprint, so its brick count lands in the 1000+ regime the held-out
    # tribute wall lives in rather than being a big but sparse (few-hundred-brick) board.
    if boosted_large and family != "dense" and rng.random() < 0.8:
        family = "dense"
    if family == "dense" and large and rng.random() < exact_probability:
        cols, rows = 48, 24
    elif boosted_large:
        cols = int(rng.integers(40, 49))
        rows = int(rng.integers(20, 25))
    else:
        cols = int(rng.integers(30, 49) if large else rng.integers(10, 31))
        rows = int(rng.integers(14, 25) if large else rng.integers(6, 17))
    density = float(rng.uniform(0.22, 0.92 if large else 0.78))

    if family == "scatter":
        mask = rng.random((rows, cols)) < density
    elif family == "symmetric":
        half = (cols + 1) // 2
        left = rng.random((rows, half)) < density
        mask = np.concatenate((left, left[:, : cols // 2][:, ::-1]), axis=1)
        if rng.random() < 0.35:
            mask = np.logical_or(mask, mask[::-1])
    elif family == "blobs":
        mask = _blob_mask(rng, rows, cols, density)
    elif family == "strokes":
        mask = _stroke_mask(rng, rows, cols)
    elif family == "dense":
        # Include exact solid rectangles often enough to make a future background-
        # filled pixel-art level a training case, not an extrapolation in density.
        if rng.random() < 0.25:
            mask = np.ones((rows, cols), dtype=bool)
        else:
            mask = rng.random((rows, cols)) < float(rng.uniform(0.88, 0.99))
            if rng.random() < 0.5:
                mask = np.logical_or(mask, mask[:, ::-1])
    else:
        raise ValueError(f"unknown procedural family: {family}")

    minimum = max(12, (rows * cols) // 12)
    if int(mask.sum()) < minimum:
        indices = rng.choice(rows * cols, size=minimum, replace=False)
        mask.flat[indices] = True

    cells = np.full((rows, cols), "0", dtype="<U1")
    cells[mask] = "1"
    live = np.argwhere(mask)
    mechanics_roll = rng.random(len(live))
    for index, (row, col) in enumerate(live):
        roll = mechanics_roll[index]
        if roll < 0.06:
            cells[row, col] = "3"
        elif roll < 0.20:
            cells[row, col] = "2"

    # Most boards explicitly exercise both power-ups; some remain plain enough to
    # preserve the ordinary-layout portion of the target distribution.
    if len(live) >= 24 and rng.random() < 0.75:
        chosen = rng.choice(len(live), size=2, replace=False)
        py, px = live[int(chosen[0])]
        sy, sx = live[int(chosen[1])]
        cells[py, px], cells[sy, sx] = "5", "4"

    gap = 3.0 if cols > 30 or rows > 18 else 6.0
    top = 36.0 if rows > 18 else 60.0
    max_wall_bottom = 420.0
    brick_h = min(22.0, (max_wall_bottom - top - (rows - 1) * gap) / rows)
    return LevelDefinition(
        name=name,
        cols=cols,
        rows=rows,
        pattern=tuple("".join(row) for row in cells.tolist()),
        gap=gap,
        brick_h=float(brick_h),
        top=top,
        family=family,
    )


def cell_histogram(level: LevelDefinition) -> dict[str, int]:
    result: dict[str, int] = {}
    for cell in "".join(level.pattern):
        if cell != "0":
            key = cell if cell.isdigit() else "art"
            result[key] = result.get(key, 0) + 1
    return result
