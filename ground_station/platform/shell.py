"""Browser-shell launcher for the ground-station dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ground_station.service.api import ApiServer

if TYPE_CHECKING:
    from ground_station.service.core import GroundStationService
    from ground_station.service.copilot import Copilot


def start_shell(
    gs_service: "GroundStationService",
    port: int = 8080,
    static_root: Path | None = None,
    experiment_runtime=None,
    copilot: "Copilot | None" = None,
    terminal_manager=None,
) -> ApiServer:
    """Create and start an API server that also serves the browser shell.

    Args:
        gs_service: The ground-station service instance.
        port: TCP port to listen on (default 8080).
        static_root: Path to the shell directory. If None, defaults to
            `<repo_root>/docs/dashboard-platform/shell`.
        experiment_runtime: ExperimentRuntime instance that receives telemetry
            ticks on every ingest_decoded call. Enables the experiment panel
            to record samples during active experiments.
        copilot: Optional Copilot instance for LLM-powered replies.
        terminal_manager: Optional TerminalManager for PTY-backed terminal WS.

    Returns:
        The started ApiServer instance.
    """
    if static_root is None:
        static_root = Path(__file__).resolve().parents[2] / "docs" / "dashboard-platform" / "shell"
    api = ApiServer(gs_service, host="0.0.0.0", port=port, static_root=static_root,
                    experiment_runtime=experiment_runtime, copilot=copilot,
                    terminal_manager=terminal_manager)
    api.start()
    return api
