from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import WebSocketDisconnect

from app.main import game_agent
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


if __name__ == "__main__":
    unittest.main()
