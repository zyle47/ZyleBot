from typing import Any

from app import db
from app.tools.base import RiskTier, tool


@tool(
    name="get_game_scores",
    description=(
        "Read the local Breakout arcade high-score table stored by this app: "
        "top entries with initials, score, level reached, and date. Use when "
        "the user asks about their Breakout / arcade / game high scores."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many entries to return (default 10, max 25).",
            },
        },
    },
    risk_tier=RiskTier.SAFE,
)
def get_game_scores(limit: int = 10) -> dict[str, Any]:
    clamped = max(1, min(limit, 25))
    return {"game": "breakout", "scores": db.top_scores(limit=clamped)}
