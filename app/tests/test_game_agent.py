from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.main import app, game_agent
from app.rl_policy import PolicyUnavailableError


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.accepted = False
        self.closed_with = None

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if not self.messages:
            raise WebSocketDisconnect()
        return self.messages.pop(0)

    async def send_json(self, message):
        self.sent.append(message)

    async def close(self, code):
        self.closed_with = code


class FakePolicy:
    def act(self, state):
        self.last_state = state
        return 2


class GameAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_and_invalid_payloads_keep_socket_alive(self) -> None:
        valid = {
            "paddle_x": 400,
            "balls": [[400, 300, 100, -300]],
            "bricks": "1" * 60,
            "speed": 340,
            "pierce": 0,
        }
        socket = FakeWebSocket([valid, ["not", "an", "object"], {**valid, "bricks": "1" * 61}])
        policy = FakePolicy()
        with patch("app.rl_policy.get_policy", return_value=policy):
            await game_agent(socket)
        self.assertTrue(socket.accepted)
        self.assertEqual(socket.sent, [{"action": 2}, {"action": 0}, {"action": 0}])
        self.assertEqual(len(policy.last_state["bricks"]), 60)

    async def test_missing_policy_reports_error_and_closes_normally(self) -> None:
        socket = FakeWebSocket([])
        with patch(
            "app.rl_policy.get_policy",
            side_effect=PolicyUnavailableError("missing"),
        ):
            await game_agent(socket)
        self.assertTrue(socket.accepted)
        self.assertEqual(socket.sent, [{"error": "no-policy"}])
        self.assertEqual(socket.closed_with, 1000)


class FakeLoadedPolicy:
    def __init__(self, training_steps: int, eval_score: float | None) -> None:
        self.observation_version = "level1-v1"
        self.training_steps = training_steps
        self.eval_score = eval_score


class GameAgentStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_status_reports_unavailable_when_no_policy(self) -> None:
        with patch("app.rl_policy.get_policy", side_effect=PolicyUnavailableError("none")):
            response = self.client.get("/api/game-agent/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"available": False})

    def test_status_reports_loaded_policy_fields(self) -> None:
        with patch("app.rl_policy.get_policy", return_value=FakeLoadedPolicy(720_000, 454.0)):
            response = self.client.get("/api/game-agent/status")
        self.assertEqual(
            response.json(),
            {
                "available": True,
                "observation_version": "level1-v1",
                "training_steps": 720_000,
                "eval_score": 454.0,
            },
        )

    def test_status_reflects_a_reloaded_policy(self) -> None:
        with patch("app.rl_policy.get_policy", return_value=FakeLoadedPolicy(100, 10.0)):
            first = self.client.get("/api/game-agent/status").json()
        with patch("app.rl_policy.get_policy", return_value=FakeLoadedPolicy(200, 20.0)):
            second = self.client.get("/api/game-agent/status").json()
        self.assertEqual(first["training_steps"], 100)
        self.assertEqual(second["training_steps"], 200)
        self.assertEqual(second["eval_score"], 20.0)


if __name__ == "__main__":
    unittest.main()
