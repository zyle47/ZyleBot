"""Zyle Learning: an isolated, layout-general Breakout RL project."""

from gymnasium.envs.registration import register, registry

ENV_ID = "ZyleBreakout-v0"

if ENV_ID not in registry:
    register(id=ENV_ID, entry_point="zl.env.breakout:BreakoutEnv")

__all__ = ["ENV_ID"]

