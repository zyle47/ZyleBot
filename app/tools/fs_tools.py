import concurrent.futures
import stat as stat_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.tools.base import RiskTier, tool

_MAX_LIST_ENTRIES = 500
_SEARCH_TIMEOUT_S = 15.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


@tool(
    name="list_directory",
    description=(
        "List the files and subfolders in a directory, with each entry's name, "
        "whether it is a folder, size in bytes, and last-modified timestamp."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative Windows path to the directory to list.",
            }
        },
        "required": ["path"],
    },
    risk_tier=RiskTier.SAFE,
)
def list_directory(path: str) -> dict[str, Any]:
    target = _resolve(path)
    if not target.exists():
        return {"error": "path not found", "path": str(target)}
    if not target.is_dir():
        return {"error": "path is not a directory", "path": str(target)}

    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        iterator = target.iterdir()
    except PermissionError:
        return {"error": "permission denied", "path": str(target)}

    for entry in iterator:
        if len(entries) >= _MAX_LIST_ENTRIES:
            truncated = True
            break
        try:
            is_dir = entry.is_dir()
            st = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size_bytes": 0 if is_dir else st.st_size,
                    "modified_at": _iso(st.st_mtime),
                    "type": "folder" if is_dir else (entry.suffix or "file"),
                }
            )
        except (PermissionError, OSError):
            # Some Windows system entries throw mid-iteration; skip, don't abort.
            entries.append({"name": entry.name, "error": "inaccessible"})

    return {"path": str(target), "entries": entries, "truncated": truncated}


@tool(
    name="read_file",
    description=(
        "Read the text contents of a file. Refuses files larger than the configured "
        "byte limit. Best-effort text decoding for non-UTF-8 files."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative Windows path to the file to read.",
            }
        },
        "required": ["path"],
    },
    risk_tier=RiskTier.SAFE,
)
def read_file(path: str) -> dict[str, Any]:
    target = _resolve(path)
    if not target.exists():
        return {"error": "path not found", "path": str(target)}
    if not target.is_file():
        return {"error": "path is not a file", "path": str(target)}

    size = target.stat().st_size
    if size > settings.tool_max_file_read_bytes:
        return {
            "error": "file too large",
            "path": str(target),
            "size_bytes": size,
            "limit_bytes": settings.tool_max_file_read_bytes,
        }

    raw = target.read_bytes()
    # utf-8-sig transparently strips a BOM if present, else behaves as utf-8.
    try:
        content = raw.decode("utf-8-sig")
        encoding_used = "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
        encoding_used = "utf-8 (lossy, non-utf8 bytes replaced)"

    return {
        "path": str(target),
        "size_bytes": size,
        "encoding_used": encoding_used,
        "content": content,
    }


def _do_search(root: Path, pattern: str, max_results: int) -> tuple[list[dict[str, Any]], bool]:
    matches: list[dict[str, Any]] = []
    truncated = False
    for match in root.rglob(pattern):
        if len(matches) >= max_results:
            truncated = True
            break
        try:
            matches.append({"path": str(match), "is_dir": match.is_dir()})
        except (PermissionError, OSError):
            continue
    return matches, truncated


@tool(
    name="search_files",
    description=(
        "Recursively search for files/folders under a root directory matching a glob "
        "pattern (e.g. '*.py', '**/*.log'). Returns matching paths."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "root": {
                "type": "string",
                "description": "Root directory to search under.",
            },
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '*.txt' or '**/*.py'.",
            },
        },
        "required": ["root", "pattern"],
    },
    risk_tier=RiskTier.SAFE,
)
def search_files(root: str, pattern: str) -> dict[str, Any]:
    root_path = _resolve(root)
    if not root_path.is_dir():
        return {"error": "root is not a directory", "path": str(root_path)}

    max_results = settings.tool_search_max_results
    # rglob over a large tree can run for a long time; enforce a wall-clock deadline.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_do_search, root_path, pattern, max_results)
        try:
            matches, truncated = future.result(timeout=_SEARCH_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            return {
                "root": str(root_path),
                "pattern": pattern,
                "matches": [],
                "timed_out": True,
            }

    return {
        "root": str(root_path),
        "pattern": pattern,
        "matches": matches,
        "truncated": truncated,
    }


@tool(
    name="get_file_info",
    description=(
        "Get metadata about a file or folder: size, created/modified/accessed "
        "timestamps, type flags, and Windows read-only attribute."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative Windows path to inspect.",
            }
        },
        "required": ["path"],
    },
    risk_tier=RiskTier.SAFE,
)
def get_file_info(path: str) -> dict[str, Any]:
    target = _resolve(path)
    try:
        st = target.stat()
    except FileNotFoundError:
        return {"error": "path not found", "path": str(target)}
    except (PermissionError, OSError) as exc:
        return {"error": str(exc), "path": str(target)}

    readonly = bool(getattr(st, "st_file_attributes", 0) & stat_module.FILE_ATTRIBUTE_READONLY) \
        if hasattr(st, "st_file_attributes") else None

    return {
        "path": str(target),
        "size_bytes": st.st_size,
        "created_at": _iso(st.st_ctime),
        "modified_at": _iso(st.st_mtime),
        "accessed_at": _iso(st.st_atime),
        "is_dir": target.is_dir(),
        "is_file": target.is_file(),
        "is_symlink": target.is_symlink(),
        "readonly": readonly,
    }
