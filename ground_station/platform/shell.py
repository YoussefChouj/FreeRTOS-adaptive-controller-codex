"""Browser-shell launcher for the ground-station dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ground_station.service.api import ApiServer

if TYPE_CHECKING:
    from ground_station.service.core import GroundStationService


def start_shell(
    gs_service: "GroundStationService",
    port: int = 8080,
    static_root: Path | None = None,
) -> ApiServer:
    """Create and start an API server that also serves the browser shell.

    Args:
        gs_service: The ground-station service instance.
        port: TCP port to listen on (default 8080).
        static_root: Path to the shell directory. If None, defaults to
            `<repo_root>/docs/dashboard-platform/shell`.

    Returns:
        The started ApiServer instance.
    """
    if static_root is None:
        static_root = Path(__file__).resolve().parents[2] / "docs" / "dashboard-platform" / "shell"
    api = ApiServer(gs_service, host="0.0.0.0", port=port, static_root=static_root)
    api.start()
    return api
