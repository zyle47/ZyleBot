from __future__ import annotations

import ast
from pathlib import Path


def test_runtime_package_has_no_rl_or_app_imports() -> None:
    package = Path(__file__).parents[1] / "zl"
    forbidden: list[str] = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name == "rl" or name.startswith("rl.") or name == "app" or name.startswith("app."):
                    forbidden.append(f"{path}:{name}")
    assert forbidden == []

