from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RiskTier(str, Enum):
    SAFE = "safe"
    CONFIRM_REQUIRED = "confirm_required"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    risk_tier: RiskTier
    func: Callable[..., dict[str, Any]]


# The single source of truth mapping tool name -> ToolSpec.
_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    parameters_schema: dict[str, Any],
    risk_tier: RiskTier = RiskTier.SAFE,
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Decorator: register a plain function as a tool.

    The wrapped function is returned unchanged (still directly callable/testable);
    the side effect is inserting a ToolSpec into the module-level registry.
    """

    def decorator(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        if name in _REGISTRY:
            raise ValueError(f"Duplicate tool registration: {name!r}")
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            risk_tier=risk_tier,
            func=func,
        )
        return func

    return decorator
