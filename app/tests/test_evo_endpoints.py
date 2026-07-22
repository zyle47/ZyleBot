from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.main import app, game_agent_evo
from app.rl_policy import PolicyUnavailableError


class FakeEvoWebSocket:
    def __init__(self, messages, slot):
        self.messages = list(messages)
        self.query_params = {"slot": slot}
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
        return 1


VALID = {
    "paddle_x": 400,
    "balls": [[400, 300, 100, -300]],
    "bricks": "1" * 60,
    "speed": 340,
    "pierce": 0,
}


class EvoWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_slot_serves_actions_and_survives_bad_payloads(self) -> None:
        socket = FakeEvoWebSocket([VALID, ["bad"], {**VALID, "bricks": "1" * 59}], slot="0")
        policy = FakePolicy()
        with patch("app.rl_policy_evo.get_evo_policy", return_value=policy):
            await game_agent_evo(socket)
        self.assertTrue(socket.accepted)
        self.assertEqual(socket.sent, [{"action": 1}, {"action": 0}, {"action": 0}])
        self.assertEqual(len(policy.last_state["bricks"]), 60)

    async def test_invalid_slot_reports_error_and_closes(self) -> None:
        socket = FakeEvoWebSocket([], slot="9")
        await game_agent_evo(socket)
        self.assertEqual(socket.sent, [{"error": "invalid-slot"}])
        self.assertEqual(socket.closed_with, 1000)

    async def test_missing_slot_param_is_invalid(self) -> None:
        socket = FakeEvoWebSocket([], slot="")
        await game_agent_evo(socket)
        self.assertEqual(socket.sent, [{"error": "invalid-slot"}])
        self.assertEqual(socket.closed_with, 1000)

    async def test_unpublished_slot_reports_no_policy(self) -> None:
        socket = FakeEvoWebSocket([], slot="0")
        with patch(
            "app.rl_policy_evo.get_evo_policy",
            side_effect=PolicyUnavailableError("no genome yet"),
        ):
            await game_agent_evo(socket)
        self.assertEqual(socket.sent, [{"error": "no-policy"}])
        self.assertEqual(socket.closed_with, 1000)


class EvoStatusRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_status_passes_through_run_state(self) -> None:
        payload = {"available": True, "generation": 42, "all_time_best": 940.0, "slots": []}
        with patch("app.rl_policy_evo.read_status", return_value=payload):
            response = self.client.get("/api/evo/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    def test_status_reports_unavailable_without_a_run(self) -> None:
        with patch("app.rl_policy_evo.read_status", return_value={"available": False}):
            response = self.client.get("/api/evo/status")
        self.assertEqual(response.json(), {"available": False})

    def test_history_passes_through_dynamic_run_artifacts(self) -> None:
        payload = {"available": True, "count": 2, "points": [{"score": 10}], "champions": []}
        with patch("app.evo_history.read_history", return_value=payload):
            response = self.client.get("/api/evo/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)


if __name__ == "__main__":
    unittest.main()
