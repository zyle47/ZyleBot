from typing import Any

from app.tools.base import RiskTier, _REGISTRY

# Import tool modules for their registration side effects.
from app.tools import fs_tools as _fs_tools  # noqa: F401,E402
from app.tools import system_tools as _system_tools  # noqa: F401,E402
from app.tools import web_tools as _web_tools  # noqa: F401,E402
from app.tools import action_tools as _action_tools  # noqa: F401,E402
from app.tools import game_tools as _game_tools  # noqa: F401,E402


def get_openai_tool_schemas(
    risk_tiers: set[RiskTier] | None = None,
) -> list[dict[str, Any]]:
    """Build the OpenAI function-calling `tools` array from the registry.

    This is the single place schema is derived from each tool's ToolSpec —
    no second hand-maintained copy exists anywhere.
    """
    schemas: list[dict[str, Any]] = []
    for spec in _REGISTRY.values():
        if risk_tiers is not None and spec.risk_tier not in risk_tiers:
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters_schema,
                },
            }
        )
    return schemas


def get_tool_risk_tier(name: str) -> RiskTier | None:
    spec = _REGISTRY.get(name)
    return spec.risk_tier if spec else None


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call by name. Never raises: unexpected errors become {'error': ...}."""
    spec = _REGISTRY.get(name)
    if spec is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return spec.func(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"invalid arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - normalize any tool failure
        return {"error": f"tool {name} failed: {exc}"}
