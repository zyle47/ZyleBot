import logging
import subprocess
from pathlib import Path
from typing import Any

from app.command_guard import Verdict, check_command
from app.config import settings
from app.platform_info import SHELL_NAME, shell_argv
from app.tools.base import RiskTier, tool
from app.tools.style_lab_tools import is_style_lab_css_path, update_style_lab_css

logger = logging.getLogger("zylebot.action_tools")


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


@tool(
    name="write_file",
    description=(
        "Create a new text file or overwrite an existing one with the given content. "
        "Creates parent folders if needed. This modifies the filesystem and normally "
        "requires confirmation. For the Style Lab, prefer update_style_lab_css; an exact "
        "style-lab.css target is safely routed through that scoped tool."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path."},
            "content": {"type": "string", "description": "Full text content to write."},
        },
        "required": ["path", "content"],
    },
    risk_tier=RiskTier.CONFIRM_REQUIRED,
)
def write_file(path: str, content: str) -> dict[str, Any]:
    # Local models sometimes choose the familiar generic writer even when the
    # dedicated scoped tool is available. Preserve the safe UX without trusting
    # that choice: the exact lab target gets the scoped validator and atomic writer.
    if is_style_lab_css_path(path):
        return update_style_lab_css(content)
    target = _resolve(path)
    if target.is_dir():
        return {"error": "path is a directory", "path": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": str(exc), "path": str(target)}
    return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}


@tool(
    name="append_file",
    description="Append text to the end of a file (creating it if it does not exist).",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to append to."},
            "content": {"type": "string", "description": "Text to append."},
        },
        "required": ["path", "content"],
    },
    risk_tier=RiskTier.CONFIRM_REQUIRED,
)
def append_file(path: str, content: str) -> dict[str, Any]:
    target = _resolve(path)
    if target.is_dir():
        return {"error": "path is a directory", "path": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return {"error": str(exc), "path": str(target)}
    return {"path": str(target), "bytes_appended": len(content.encode("utf-8"))}


@tool(
    name="make_directory",
    description="Create a directory (and any missing parent directories).",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to create."},
        },
        "required": ["path"],
    },
    risk_tier=RiskTier.CONFIRM_REQUIRED,
)
def make_directory(path: str) -> dict[str, Any]:
    target = _resolve(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"error": str(exc), "path": str(target)}
    return {"path": str(target), "created": True}


@tool(
    name="run_command",
    description=(
        f"Run a shell command in the local machine's native shell ({SHELL_NAME}) and "
        "return its output. Can read, modify, or run most things the user can, but "
        "destructive commands are refused outright — deleting/formatting, killing or "
        "stopping processes, elevation, and encoded or nested-shell execution will not "
        "run. Use only when the user clearly wants an action performed, "
        f"and write commands for {SHELL_NAME}."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": f"The {SHELL_NAME} command to run."},
            "cwd": {
                "type": "string",
                "description": "Optional working directory to run the command in.",
            },
        },
        "required": ["command"],
    },
    risk_tier=RiskTier.CONFIRM_REQUIRED,
)
def run_command(command: str, cwd: str | None = None) -> dict[str, Any]:
    verdict = check_command(command)
    logger.info(
        "command_guard verdict=%s rule=%s command=%r",
        verdict.verdict.value, verdict.rule, command,
    )
    if verdict.verdict is Verdict.BLOCK:
        return {
            "error": "blocked_by_command_guard",
            "reason": verdict.reason,
            "rule": verdict.rule,
        }

    workdir = None
    if cwd:
        wd = _resolve(cwd)
        if not wd.is_dir():
            return {"error": "cwd is not a directory", "cwd": str(wd)}
        workdir = str(wd)

    try:
        proc = subprocess.run(
            shell_argv(command),
            capture_output=True,
            text=True,
            # Command output isn't guaranteed to match the locale codec; a
            # strict decode would crash the reader thread mid-capture.
            errors="replace",
            timeout=settings.command_timeout_s,
            cwd=workdir,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"command timed out after {settings.command_timeout_s}s", "command": command}
    except OSError as exc:
        return {"error": str(exc), "command": command}

    cap = settings.command_max_output_chars

    def _cap(s: str) -> tuple[str, bool]:
        return (s[:cap], len(s) > cap)

    stdout, out_trunc = _cap(proc.stdout or "")
    stderr, err_trunc = _cap(proc.stderr or "")
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": out_trunc or err_trunc,
    }
