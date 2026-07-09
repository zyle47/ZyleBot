import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("zylebot.model_manager")

# Editable config mapping LM Studio model id -> {alias, context_length}.
# Lives at the project root so it can be tuned without touching code.
_MODELS_CONFIG_PATH = Path("models.json")


def load_models_config() -> dict[str, dict[str, Any]]:
    """Read models.json fresh each call so edits apply without a restart."""
    try:
        return json.loads(_MODELS_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Could not read models.json: %s", exc)
        return {}


def get_alias(model_id: str) -> str | None:
    return load_models_config().get(model_id, {}).get("alias")


def get_context_length(model_id: str) -> int | None:
    return load_models_config().get(model_id, {}).get("context_length")


def _lms_path() -> str:
    """Locate the LM Studio CLI: PATH first, then the default install location."""
    found = shutil.which("lms")
    if found:
        return found
    home = Path.home()
    for candidate in (home / ".lmstudio" / "bin" / "lms.exe", home / ".lmstudio" / "bin" / "lms"):
        if candidate.exists():
            return str(candidate)
    return "lms"  # last resort; will error clearly if truly missing


def load_model(model_id: str, context_length: int | None) -> dict[str, Any]:
    """Switch LM Studio to `model_id`: unload everything first (so only one model
    occupies VRAM), then load with the requested context. Blocking — call via a
    thread from async code. Returns {"ok": bool, "error"?: str}."""
    lms = _lms_path()
    try:
        # Unload all so we never end up with two models on a single GPU.
        subprocess.run(
            [lms, "unload", "--all"], capture_output=True, text=True, timeout=60
        )
        cmd = [lms, "load", model_id, "--gpu", "max", "-y"]
        if context_length:
            cmd += ["-c", str(context_length)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return {"ok": False, "error": "the 'lms' CLI was not found (is LM Studio installed?)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "model load timed out"}

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "load failed").strip()
        return {"ok": False, "error": detail[:500]}
    return {"ok": True}
