from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
