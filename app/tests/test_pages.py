from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

# Board DOM ids game.js binds to — the shared board must preserve every one.
BOARD_IDS = (
    'id="game-root"',
    'id="game-canvas"',
    'id="hud-score"',
    'id="hud-level"',
    'id="hud-lives"',
    'id="ai-btn"',
    'id="mute-btn"',
    'id="score-form"',
    'id="initials-input"',
    'id="panel-attract"',
)


class GamePageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ordinary_game_renders_shared_board_with_page_chrome(self) -> None:
        html = self.client.get("/game").text
        for board_id in BOARD_IDS:
            self.assertIn(board_id, html)
        self.assertIn("page-header", html)     # full page keeps the site chrome
        self.assertNotIn("game-embed", html)   # ...and is not the embed body
        self.assertIn("/game/arena", html)     # AI ARENA link on the game page

    def test_embed_true_selects_board_only_markup(self) -> None:
        html = self.client.get("/game?embed=1").text
        for board_id in BOARD_IDS:
            self.assertIn(board_id, html)      # same board ids preserved
        self.assertIn("game-embed", html)      # dedicated body class
        self.assertNotIn("page-header", html)  # no site header/footer

    def test_embed_false_or_absent_keeps_full_page(self) -> None:
        for url in ("/game", "/game?embed=0", "/game?embed=false"):
            html = self.client.get(url).text
            self.assertIn("page-header", html, url)
            self.assertNotIn("game-embed", html, url)

    def test_arena_page_offers_capped_controls(self) -> None:
        html = self.client.get("/game/arena").text
        self.assertIn("AI ARENA", html)
        self.assertIn('data-default-games="4"', html)  # default 4
        self.assertIn('data-max-games="6"', html)       # hard cap 6
        self.assertIn("game-arena.js", html)
        for count in ("1", "2", "4", "6"):
            self.assertIn(f'data-count="{count}"', html)

    def test_evolution_page_wires_the_evo_viewer(self) -> None:
        champions = [
            {"label": "DQN", "score": 683.4, "date": "NOW", "detail": "dqn", "run": "dqn"},
            {"label": "EVOLUTION", "score": 1128.8, "date": "NOW", "detail": "evo", "run": "evo"},
            {
                "label": "CURRICULUM EVO",
                "score": 1586.45,
                "date": "NOW",
                "detail": "curriculum",
                "run": "curriculum",
            },
        ]
        points = [
            {"label": "5K SMOKE", "score": 48, "kind": "training", "measure": "DQN TRAINING / GATE"},
            {"label": "GEN 43", "score": 1441.9, "kind": "validation", "measure": "SAVED VALIDATION HIGH"},
            {"label": "GEN 151", "score": 1961.6, "kind": "validation", "measure": "SAVED VALIDATION HIGH"},
        ]
        snapshot = {"available": True, "count": len(points), "points": points, "champions": champions}
        with patch("app.pages.evo_history.read_history", return_value=snapshot):
            html = self.client.get("/game/evolution").text
        self.assertIn("// EVOLUTION", html)
        self.assertIn("game-evolution.js", html)
        self.assertIn("evo=1", html)              # boards target the evolved-genome WS
        self.assertIn('id="evo-chart"', html)     # the fitness chart canvas
        self.assertIn('id="evo-grid"', html)
        self.assertIn('id="evo-history-chart"', html)
        self.assertIn('id="evo-history-data"', html)
        self.assertIn("HISTORICAL PROGRESS", html)
        self.assertIn("RECORDED STOPS", html)
        self.assertIn("SAVED VALIDATION HIGH", html)
        for label, score in (("DQN", "683.4"), ("EVOLUTION", "1128.8"), ("CURRICULUM EVO", "1586.45")):
            self.assertIn(label, html)
            self.assertIn(f'data-score="{score}"', html)
        for intermediate in ("5K SMOKE", "GEN 43", "1441.9", "GEN 151", "1961.6"):
            self.assertIn(intermediate, html)


if __name__ == "__main__":
    unittest.main()
