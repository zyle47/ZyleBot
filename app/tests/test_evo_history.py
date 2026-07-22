from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import evo_history


class EvoHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dqn = self.root / "runs"
        self.evo = self.root / "runs_evo"
        self.policy = self.root / "policy"
        self.dqn.mkdir()
        self.evo.mkdir()
        self.policy.mkdir()
        self.patchers = (
            patch.object(evo_history, "DQN_RUNS_DIR", self.dqn),
            patch.object(evo_history, "EVO_RUNS_DIR", self.evo),
            patch.object(evo_history, "POLICY_META_PATH", self.policy / "meta.json"),
            patch.object(evo_history, "DEPLOYMENT_HISTORY_PATH", self.policy / "history.json"),
        )
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_builds_history_from_gates_generations_rotations_and_champions(self) -> None:
        dqn_run = self.dqn / "20260101-010101"
        self.write(
            dqn_run / "log.csv",
            "steps,eval_score_mean\n5000,48\n10000,55\n",
        )
        self.write(
            dqn_run / "gates.csv",
            "steps,attempt,candidate_mean,episodes,accepted\n"
            "5000,0,48,50,True\n20000,1,75,200,True\n30000,2,80,200,False\n",
        )

        evo_run = self.evo / "20260102-020202"
        self.write(
            evo_run / "evo_log.csv",
            "generation,all_time_best,val_rotation\n"
            "0,100,0\n1,100,0\n2,80,1\n3,120,1\n",
        )
        self.write(evo_run / "run.json", json.dumps({"curriculum_clear_max": 0.6}))
        self.write(
            evo_run / "audit-20260102-030303.json",
            json.dumps(
                {
                    "accepted": True,
                    "candidate_mean": 130,
                    "episodes": 200,
                    "sort_key": "20260102-020202-999999",
                }
            ),
        )
        champion = {
            "label": "DEPLOYED",
            "score": 90,
            "date": "02 JAN 2026",
            "detail": "test",
            "run": "test-run",
            "phase": "DQN",
            "sort_key": "20260101-999999",
        }
        self.write(self.policy / "history.json", json.dumps([champion]))
        self.write(self.policy / "meta.json", json.dumps({"eval_score": 90, "training_steps": 20_000}))

        result = evo_history.read_history()

        self.assertEqual(result["count"], 7)
        self.assertEqual(len(result["champions"]), 2)
        self.assertEqual(
            [point["score"] for point in result["points"]],
            [48.0, 75.0, 90.0, 100.0, 80.0, 120.0, 130.0],
        )
        self.assertEqual(result["points"][4]["measure"], "Validation rotation re-score")
        self.assertEqual(result["points"][-1]["phase"], "CURRICULUM EVO")
        self.assertEqual(result["points"][-1]["kind"], "champion")

    def test_unknown_current_policy_is_added_without_source_edits(self) -> None:
        self.write(self.policy / "history.json", "[]")
        self.write(self.policy / "meta.json", json.dumps({"eval_score": 321.5, "training_steps": 42}))

        result = evo_history.read_history()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["champions"][0]["label"], "CURRENT DEPLOYED")
        self.assertEqual(result["champions"][0]["score"], 321.5)
