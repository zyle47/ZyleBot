"""Fail-closed guard for run_command: classifies a shell command as BLOCK
(never runs), CONFIRM (human approval, today's default), or ALLOW (known
read-only, skips confirmation). Threat model: a small local model writing
destructive commands the obvious way, not a determined adversary building
strings dynamically at runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.config import settings


class Verdict(str, Enum):
    BLOCK = "block"
    CONFIRM = "confirm"
    ALLOW = "allow"


@dataclass
class GuardResult:
    verdict: Verdict
    reason: str
    rule: str


_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# PowerShell aliases -> canonical cmdlet name. Verified against this machine's
# actual `Get-Alias` output — don't add entries from memory without checking.
_ALIASES: dict[str, str] = {
    "rm": "remove-item",
    "ri": "remove-item",
    "del": "remove-item",
    "erase": "remove-item",
    "rd": "remove-item",
    "rmdir": "remove-item",
    "rp": "remove-itemproperty",
    "clc": "clear-content",
    "iex": "invoke-expression",
    "mv": "move-item",
    "move": "move-item",
    "mi": "move-item",
    "kill": "stop-process",
    "spps": "stop-process",
    "start": "start-process",
    "saps": "start-process",
    "sc": "set-content",
    "ls": "get-childitem",
    "dir": "get-childitem",
    "gci": "get-childitem",
    "cat": "get-content",
    "type": "get-content",
    "gc": "get-content",
    "pwd": "get-location",
    "gl": "get-location",
    "write": "write-output",
    "echo": "write-output",
    "tee": "tee-object",
}

_BLOCKED_VERBS = {
    # irreversible destruction
    "remove-item", "remove-itemproperty", "clear-content", "clear-disk",
    "format-volume", "format", "diskpart", "bcdedit", "vssadmin", "wbadmin",
    "cipher",
    # machine state
    "stop-computer", "restart-computer", "shutdown", "stop-process",
    "stop-service", "set-executionpolicy",
    # laundering: runs other code, which would make every other rule bypassable
    "invoke-expression", "powershell", "pwsh", "cmd", "wsl", "bash", "sh",
    "start-process",
}

# These have legitimate non-destructive uses, so they only block when paired
# with an inline-code flag (see _INLINE_CODE_FLAGS) rather than unconditionally.
_CONDITIONAL_INTERPRETERS = {"python", "py", "node"}
_INLINE_CODE_FLAGS = {"-c", "-e", "-command", "--eval", "--command"}

_SCRIPT_EXTENSIONS = (".ps1", ".bat", ".cmd")

# Move-Item isn't inherently destructive, but combined with a wildcard it can
# silently clobber a whole directory. Remove-Item is already always blocked
# above regardless of wildcard; this only adds real coverage for move.
_DELETE_OR_MOVE_VERBS = {"remove-item", "move-item"}

_ALLOWED_VERBS = {"write-output", "whoami", "hostname", "ping"}
_GIT_READONLY_SUBCOMMANDS = {"status", "log", "diff", "show", "branch"}
_REDIRECT_VERBS = {"out-file", "set-content", "tee-object"}

_PROTECTED_BARE = {"c:\\windows", "c:\\users", "$env:userprofile", "~"}
_DRIVE_ROOT_RE = re.compile(r"[a-z]:")


def _protected_project_paths() -> set[str]:
    db = Path(settings.db_path)
    db_abs = str((_PROJECT_ROOT / db).resolve()).lower()
    git_abs = str((_PROJECT_ROOT / ".git").resolve()).lower()
    return {
        ".git", git_abs,
        db.name.lower(),
        str(db).lower(), str(db).lower().replace("\\", "/"),
        db_abs,
    }


_PROTECTED_PATHS = _protected_project_paths()


def _split_segments(command: str) -> list[str]:
    """Split on ; / newline / | / && / ||, but never inside quotes — a
    quoted payload (e.g. `python -c "a; b"`) must survive as one token."""
    segments: list[str] = []
    current: list[str] = []
    in_single = in_double = False
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if in_single:
            current.append(ch)
            in_single = ch != "'"
            i += 1
            continue
        if in_double:
            current.append(ch)
            in_double = ch != '"'
            i += 1
            continue
        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
        elif ch == '"':
            in_double = True
            current.append(ch)
            i += 1
        elif ch in (";", "\n", "\r"):
            segments.append("".join(current))
            current = []
            i += 1
        elif ch == "|":
            segments.append("".join(current))
            current = []
            i += 2 if command[i + 1 : i + 2] == "|" else 1
        elif ch == "&" and command[i + 1 : i + 2] == "&":
            segments.append("".join(current))
            current = []
            i += 2
        else:
            current.append(ch)
            i += 1
    segments.append("".join(current))
    return [s.strip() for s in segments if s.strip()]


def _tokenize(segment: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_single = in_double = False
    for ch in segment:
        if in_single:
            current.append(ch)
            in_single = ch != "'"
        elif in_double:
            current.append(ch)
            in_double = ch != '"'
        elif ch == "'":
            in_single = True
            current.append(ch)
        elif ch == '"':
            in_double = True
            current.append(ch)
        elif ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _basename(token: str) -> str:
    name = token.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".exe") else name


def _blocked_argument(tokens: list[str]) -> tuple[str, str] | None:
    if any(t.startswith("-enc") for t in tokens):
        return ("uses -EncodedCommand (base64-obscured payload)", "encoded_command")
    if "-force" in tokens and "-recurse" in tokens:
        return ("combines -Force with -Recurse", "force_recurse_combo")
    if "-confirm:$false" in tokens:
        return ("disables PowerShell's own confirmation prompt", "confirm_false")
    for i, t in enumerate(tokens[:-1]):
        if t == "-verb" and tokens[i + 1] == "runas":
            return ("requests elevation via -Verb RunAs", "runas_elevation")
    return None


def _match_protected_path(tokens: list[str]) -> str | None:
    for raw in tokens:
        t = raw.strip("'\"")
        if t in ("\\", "/"):
            return t
        norm = t.rstrip("\\/")
        if _DRIVE_ROOT_RE.fullmatch(norm):
            return t
        if norm in _PROTECTED_BARE or norm in _PROTECTED_PATHS:
            return t
    return None


def _has_redirect(text: str, tokens: list[str]) -> bool:
    if ">" in text:
        return True
    for t in tokens:
        base = _basename(t.strip("'\""))
        if _ALIASES.get(base, base) in _REDIRECT_VERBS:
            return True
    return False


def _check_segment(segment: str) -> GuardResult:
    text = segment.strip().lower()
    tokens = _tokenize(text)
    if not tokens:
        return GuardResult(Verdict.BLOCK, "segment has no runnable command", "unparseable_segment")

    verb_token = tokens[0].strip("'\"")
    if verb_token == "&" and len(tokens) > 1:
        verb_token = tokens[1].strip("'\"")

    base = _basename(verb_token)

    if base.endswith(_SCRIPT_EXTENSIONS):
        return GuardResult(Verdict.BLOCK, f"executes a script file ({base})", "script_execution")

    canonical = _ALIASES.get(base, base)

    if canonical in _BLOCKED_VERBS:
        return GuardResult(Verdict.BLOCK, f"'{canonical}' is not allowed", "blocked_verb")

    if canonical == "reg" and len(tokens) > 1 and tokens[1].strip("'\"") == "delete":
        return GuardResult(Verdict.BLOCK, "'reg delete' modifies the registry", "reg_delete")

    if canonical in _CONDITIONAL_INTERPRETERS and any(t in _INLINE_CODE_FLAGS for t in tokens):
        return GuardResult(Verdict.BLOCK, f"'{canonical}' invoked with an inline-code flag", "inline_interpreter")

    blocked = _blocked_argument(tokens)
    if blocked:
        reason, rule = blocked
        return GuardResult(Verdict.BLOCK, reason, rule)

    protected = _match_protected_path(tokens)
    if protected:
        return GuardResult(Verdict.BLOCK, f"targets a protected path ({protected})", "protected_path")

    if canonical in _DELETE_OR_MOVE_VERBS and "*" in text:
        return GuardResult(Verdict.BLOCK, f"'{canonical}' with a wildcard target", "wildcard_delete_move")

    if canonical == "git":
        if len(tokens) == 2 and tokens[1] in _GIT_READONLY_SUBCOMMANDS:
            return GuardResult(Verdict.ALLOW, "read-only git subcommand", "allow_git_readonly")
        return GuardResult(Verdict.CONFIRM, "git subcommand not in the read-only allowlist", "default_confirm")

    if canonical in _ALLOWED_VERBS or canonical.startswith("get-"):
        if _has_redirect(text, tokens):
            return GuardResult(Verdict.CONFIRM, "read-only command but redirects output", "redirect_disqualifies_allow")
        return GuardResult(Verdict.ALLOW, f"'{canonical}' is read-only", "allow_verb")

    return GuardResult(Verdict.CONFIRM, "not on the read-only allowlist", "default_confirm")


def check_command(command: str) -> GuardResult:
    if not command or not command.strip():
        return GuardResult(Verdict.BLOCK, "empty command", "empty_command")

    segments = _split_segments(command.replace("`", ""))
    if not segments:
        return GuardResult(Verdict.BLOCK, "command has no runnable segment", "unparseable")

    results = [_check_segment(seg) for seg in segments]

    for r in results:
        if r.verdict is Verdict.BLOCK:
            return r

    if all(r.verdict is Verdict.ALLOW for r in results):
        return results[0]

    return GuardResult(Verdict.CONFIRM, "not recognized as read-only; human confirmation required", "default_confirm")
