import os
import platform

_SYSTEM = platform.system()  # 'Windows' | 'Darwin' | 'Linux' | ...

if _SYSTEM == "Windows":
    OS_NAME = "Windows"
    SHELL_NAME = "PowerShell"
elif _SYSTEM == "Darwin":
    OS_NAME = "macOS"
    SHELL_NAME = os.path.basename(os.environ.get("SHELL", "/bin/bash")) or "bash"
else:
    OS_NAME = _SYSTEM or "Linux"
    SHELL_NAME = os.path.basename(os.environ.get("SHELL", "/bin/bash")) or "bash"


def shell_argv(command: str) -> list[str]:
    """Return the subprocess argv that runs `command` in the OS's native shell.

    Windows uses PowerShell; macOS/Linux use the user's $SHELL (default bash).
    """
    if _SYSTEM == "Windows":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    shell = os.environ.get("SHELL", "/bin/bash")
    return [shell, "-c", command]
