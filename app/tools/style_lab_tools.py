import os
import re
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.tools.base import RiskTier, tool

_STATIC_DIR = (Path(__file__).resolve().parents[1] / "static").resolve()
_STYLE_LAB_CSS = _STATIC_DIR / "style-lab.css"
_STYLE_LAB_DEFAULT_CSS = _STATIC_DIR / "style-lab.default.css"
_MAX_CSS_BYTES = 128 * 1024

_FORBIDDEN_CSS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"@import\b", re.IGNORECASE), "@import is not allowed"),
    (re.compile(r"url\s*\(", re.IGNORECASE), "url() is not allowed"),
    (re.compile(r"\b(?:https?|ftp|file):", re.IGNORECASE), "external protocols are not allowed"),
    (re.compile(r"</?style\b", re.IGNORECASE), "HTML style tags are not allowed"),
    (re.compile(r"expression\s*\(", re.IGNORECASE), "CSS expressions are not allowed"),
    (re.compile(r"\bbehavior\s*:", re.IGNORECASE), "CSS behaviors are not allowed"),
)


def is_style_lab_css_path(path: str) -> bool:
    """Return true only when a caller resolves to the fixed editable lab file."""
    try:
        return Path(path).expanduser().resolve() == _STYLE_LAB_CSS
    except (OSError, RuntimeError, ValueError):
        return False


def _target_is_safe(target: Path) -> bool:
    """Fail closed if the fixed lab file or its directory became a link."""
    try:
        if target.parent.resolve() != _STATIC_DIR:
            return False
        if target.exists():
            is_junction = bool(getattr(target, "is_junction", lambda: False)())
            if target.is_symlink() or is_junction or not target.is_file():
                return False
            if target.resolve() != target:
                return False
        return True
    except OSError:
        return False


def _balanced_css(content: str) -> bool:
    """Check braces while ignoring strings and comments.

    Browsers recover from many CSS syntax errors, but refusing an unfinished
    block catches the most common model truncation before it replaces the lab.
    """
    depth = 0
    state = "normal"
    i = 0
    while i < len(content):
        char = content[i]
        nxt = content[i + 1] if i + 1 < len(content) else ""
        if state == "comment":
            if char == "*" and nxt == "/":
                state = "normal"
                i += 2
                continue
        elif state in {"single", "double"}:
            if char == "\\":
                i += 2
                continue
            if (state == "single" and char == "'") or (state == "double" and char == '"'):
                state = "normal"
        else:
            if char == "/" and nxt == "*":
                state = "comment"
                i += 2
                continue
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    return False
        i += 1
    return depth == 0 and state == "normal"


def _validate_css(content: str) -> str | None:
    if not isinstance(content, str):
        return "content must be a string"
    if not content.strip():
        return "stylesheet cannot be empty"
    if "\x00" in content:
        return "stylesheet cannot contain null bytes"
    size = len(content.encode("utf-8"))
    if size > _MAX_CSS_BYTES:
        return f"stylesheet exceeds the {_MAX_CSS_BYTES}-byte limit"
    for pattern, message in _FORBIDDEN_CSS:
        if pattern.search(content):
            return message
    if not _balanced_css(content):
        return "stylesheet has unbalanced braces, strings, or comments"
    return None


def _atomic_write(target: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".style-lab-", suffix=".tmp", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_lab_css(content: str) -> dict[str, Any]:
    error = _validate_css(content)
    if error:
        return {"error": error, "file": "app/static/style-lab.css"}
    if not _target_is_safe(_STYLE_LAB_CSS):
        return {"error": "protected Style Lab target failed its path safety check"}
    try:
        _atomic_write(_STYLE_LAB_CSS, content)
    except OSError as exc:
        return {"error": str(exc), "file": "app/static/style-lab.css"}
    encoded = content.encode("utf-8")
    return {
        "file": "app/static/style-lab.css",
        "bytes_written": len(encoded),
        "sha256": sha256(encoded).hexdigest(),
        "scope": "isolated /style-lab preview only",
    }


@tool(
    name="update_style_lab_css",
    description=(
        "Replace the CSS used only inside the isolated /style-lab preview. This is the "
        "only automatically writable file; the destination is fixed and cannot be changed. "
        "Provide the complete stylesheet, not a patch. External resources are prohibited."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The complete CSS stylesheet for the isolated Style Lab preview.",
            }
        },
        "required": ["content"],
    },
    risk_tier=RiskTier.SCOPED_WRITE,
)
def update_style_lab_css(content: str) -> dict[str, Any]:
    return _write_lab_css(content)


@tool(
    name="reset_style_lab_css",
    description=(
        "Reset only the isolated /style-lab preview stylesheet to its protected starter design."
    ),
    parameters_schema={"type": "object", "properties": {}},
    risk_tier=RiskTier.SCOPED_WRITE,
)
def reset_style_lab_css() -> dict[str, Any]:
    if not _target_is_safe(_STYLE_LAB_DEFAULT_CSS):
        return {"error": "protected Style Lab default failed its path safety check"}
    try:
        content = _STYLE_LAB_DEFAULT_CSS.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": str(exc), "file": "app/static/style-lab.default.css"}
    return _write_lab_css(content)
