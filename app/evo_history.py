"""Build the Breakout training-history feed from local run artifacts.

The evolution page polls this module through ``GET /api/evo/history``. CSV and
JSON files under ``rl/runs*`` are the source of truth, so a running evolution
job appears here as soon as it appends a generation; the UI needs no source edit.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DQN_RUNS_DIR = PROJECT_ROOT / "rl" / "runs"
EVO_RUNS_DIR = PROJECT_ROOT / "rl" / "runs_evo"
POLICY_META_PATH = PROJECT_ROOT / "rl" / "policy" / "meta.json"
DEPLOYMENT_HISTORY_PATH = PROJECT_ROOT / "rl" / "policy" / "history.json"


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def _display_date(sort_key: str) -> str:
    try:
        return datetime.strptime(sort_key[:8], "%Y%m%d").strftime("%d %b %Y").upper()
    except ValueError:
        return "LOCAL RUN"


def _dqn_points() -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    if not DQN_RUNS_DIR.is_dir():
        return points

    # Keep one genuine origin rather than charting every noisy 10-game eval.
    smoke_candidates: list[tuple[str, dict[str, str]]] = []
    for log_path in sorted(DQN_RUNS_DIR.glob("*/log.csv")):
        for row in _read_csv(log_path):
            if _int(row.get("steps")) == 5000:
                smoke_candidates.append((log_path.parent.name, row))
                break
    if smoke_candidates:
        run, row = min(smoke_candidates, key=lambda item: item[0])
        score = _finite_float(row.get("eval_score_mean"))
        if score is not None:
            points.append(
                {
                    "label": "5K SMOKE",
                    "score": score,
                    "phase": "DQN",
                    "kind": "training",
                    "measure": "Greedy evaluation",
                    "source": f"{run} / step 5K",
                    "sort_key": f"{run}-000005000",
                }
            )

    # Accepted attempts are the statistically gated DQN breakthroughs. Attempt
    # zero only re-establishes a fork baseline, so it is intentionally excluded.
    for gate_path in sorted(DQN_RUNS_DIR.glob("*/gates.csv")):
        run = gate_path.parent.name
        for row in _read_csv(gate_path):
            if row.get("accepted", "").lower() != "true" or _int(row.get("attempt")) <= 0:
                continue
            score = _finite_float(row.get("candidate_mean"))
            if score is None:
                continue
            steps = _int(row.get("steps"))
            episodes = _int(row.get("episodes"))
            points.append(
                {
                    "label": f"{steps / 1_000_000:g}M GATE" if steps >= 1_000_000 else f"{steps // 1000}K GATE",
                    "score": score,
                    "phase": "DQN",
                    "kind": "training",
                    "measure": f"Accepted {episodes}-game gate mean",
                    "source": f"{run} / step {steps:,}",
                    "sort_key": f"{run}-{steps:09d}",
                }
            )
    return points


def _run_phase(run: str, metadata: dict[str, Any]) -> str:
    clear_max = _finite_float(metadata.get("curriculum_clear_max"))
    if clear_max is not None and clear_max > 0:
        return "CURRICULUM EVO"
    # Metadata was added after the first runs; known later runs descend from the
    # curriculum champion and used curriculum experiments.
    if run >= "20260721-180231":
        return "CURRICULUM EVO"
    return "EVOLUTION"


def _evo_points() -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    if not EVO_RUNS_DIR.is_dir():
        return points

    for run_dir in sorted(path for path in EVO_RUNS_DIR.iterdir() if path.is_dir()):
        run = run_dir.name
        rows = _read_csv(run_dir / "evo_log.csv")
        if not rows:
            continue
        metadata = _read_json(run_dir / "run.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        phase = _run_phase(run, metadata)
        last_best: float | None = None
        last_rotation: int | None = None
        run_points: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            best = _finite_float(row.get("all_time_best"))
            if best is None:
                continue
            generation = _int(row.get("generation"), index)
            rotation = _int(row.get("val_rotation"), 0)
            changed = last_best is None or not math.isclose(best, last_best, abs_tol=0.049)
            rotated = last_rotation is not None and rotation != last_rotation
            if changed or rotated:
                measure = "Validation rotation re-score" if rotated and best <= (last_best or best) else "Saved validation high"
                run_points.append(
                    {
                        "label": f"GEN {generation}",
                        "score": best,
                        "phase": phase,
                        "kind": "validation",
                        "measure": measure,
                        "source": f"{run} / gen {generation} / rotation {rotation}",
                        "sort_key": f"{run}-{generation:06d}",
                        "run": run,
                    }
                )
            last_best = best
            last_rotation = rotation
        if run_points:
            run_points[-1]["highlight"] = True
            points.extend(run_points)

        for audit_path in sorted(run_dir.glob("audit*.json")):
            audit = _read_json(audit_path, {})
            if not isinstance(audit, dict) or not audit.get("accepted"):
                continue
            score = _finite_float(audit.get("candidate_mean"))
            if score is None:
                continue
            sort_key = str(audit.get("sort_key") or f"{run}-999999")
            points.append(
                {
                    "label": "AUDIT PASSED",
                    "score": score,
                    "phase": phase,
                    "kind": "champion",
                    "measure": f"Passed {audit.get('episodes', '?')}-game paired audit",
                    "source": f"{run} / fresh seeds",
                    "sort_key": sort_key,
                    "run": run,
                    "highlight": True,
                }
            )
    return points


def _champions() -> list[dict[str, Any]]:
    payload = _read_json(DEPLOYMENT_HISTORY_PATH, [])
    champions = [dict(item) for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    # If the policy was replaced without using history-aware tooling, still show
    # the current deployment immediately. Its prior audit/run remains in the
    # scanned artifacts, while this point reflects what the app actually serves.
    meta = _read_json(POLICY_META_PATH, {})
    score = _finite_float(meta.get("eval_score")) if isinstance(meta, dict) else None
    known_scores = {_finite_float(item.get("score")) for item in champions}
    if score is not None and score not in known_scores:
        try:
            stamp = POLICY_META_PATH.stat().st_mtime
            sort_key = datetime.fromtimestamp(stamp).strftime("%Y%m%d-%H%M%S")
        except OSError:
            sort_key = datetime.now().strftime("%Y%m%d-%H%M%S")
        champions.append(
            {
                "label": "CURRENT DEPLOYED",
                "score": score,
                "date": _display_date(sort_key),
                "detail": f"{_int(meta.get('training_steps')):,}-step deployed policy",
                "run": "rl/policy/meta.json",
                "phase": "DEPLOYED",
                "measure": "Current deployed evaluation",
                "sort_key": sort_key,
            }
        )
    return sorted(champions, key=lambda item: str(item.get("sort_key", "")))


def read_history() -> dict[str, Any]:
    """Return the complete dynamic history consumed by the evolution page."""
    champions = _champions()
    evo_points = _evo_points()
    known_champion_scores = {
        round(float(score), 6)
        for item in champions
        if (score := _finite_float(item.get("score"))) is not None
    }
    for point in evo_points:
        score = _finite_float(point.get("score"))
        if point.get("kind") != "champion" or score is None or round(score, 6) in known_champion_scores:
            continue
        champions.append(
            {
                "label": point.get("label", "AUDIT PASSED"),
                "score": score,
                "date": _display_date(str(point.get("sort_key", ""))),
                "detail": point.get("measure", "Passed paired audit"),
                "run": point.get("run", point.get("source", "local run")),
                "phase": point.get("phase", "EVOLUTION"),
                "measure": point.get("measure", "Passed paired audit"),
                "sort_key": point.get("sort_key", ""),
            }
        )
        known_champion_scores.add(round(score, 6))
    champions.sort(key=lambda item: str(item.get("sort_key", "")))

    points = [*_dqn_points(), *evo_points]
    existing_champion_scores = {
        round(float(score), 6)
        for item in points
        if item.get("kind") == "champion"
        if (score := _finite_float(item.get("score"))) is not None
    }
    for champion in champions:
        score = _finite_float(champion.get("score"))
        if score is not None and round(score, 6) in existing_champion_scores:
            continue
        point = dict(champion)
        point.update(kind="champion", highlight=True)
        point.setdefault("source", str(point.get("run", "deployed policy")))
        points.append(point)
        if score is not None:
            existing_champion_scores.add(round(score, 6))

    points.sort(key=lambda item: str(item.get("sort_key", "")))
    return {
        "available": bool(points),
        "count": len(points),
        "points": points,
        "champions": champions,
    }
