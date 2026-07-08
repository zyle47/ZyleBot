import platform
from typing import Any

import psutil

from app.tools.base import RiskTier, tool


@tool(
    name="get_system_info",
    description=(
        "Get read-only system information: OS version, CPU count and usage, RAM "
        "usage, and per-drive disk usage."
    ),
    parameters_schema={
        "type": "object",
        "properties": {},
    },
    risk_tier=RiskTier.SAFE,
)
def get_system_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()

    disks: list[dict[str, Any]] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent_used": usage.percent,
                }
            )
        except (PermissionError, OSError):
            # Empty optical/removable drives throw on stat; skip them.
            disks.append({"device": part.device, "mountpoint": part.mountpoint, "error": "inaccessible"})

    return {
        "os": platform.platform(),
        "os_version": platform.version(),
        "cpu": {
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "percent_used": psutil.cpu_percent(interval=0.2),
        },
        "memory": {
            "total_bytes": vm.total,
            "available_bytes": vm.available,
            "percent_used": vm.percent,
        },
        "disks": disks,
    }
