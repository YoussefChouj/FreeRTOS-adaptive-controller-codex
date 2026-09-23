"""Dry-run workflow: execute in sim and store a Run of kind 'validation'.

Entry point: ``dry_run(workflow_or_trajectory, params) -> Run``
which executes a simulated trajectory and stores the result as a
validation Run tagged ``sim``.

Uses T2's Run/store pipeline (ground_station.research.Run and Store).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..run import Run
from ..store import Store
from .plant import IdentifiedPlant
from .baseline import CascadedPID
from .reference_model import ReferenceModel
from .replay import Replay, ReplayResult
from .constants import DEFAULT_DT


def dry_run(
    workflow_or_trajectory: Any,
    params: Optional[dict[str, Any]] = None,
    *,
    dt: float = DEFAULT_DT,
    store: Optional[Store] = None,
    kind: str = "validation",
    tags: Optional[list[str]] = None,
) -> Run:
    """Execute a simulation and store the result as a Run.

    Args:
        workflow_or_trajectory: a trajectory spec (list of capture dicts with
            ``'t'`` and per-axis setpoints) or a dict with a ``'trajectory'``
            key. May also be a string path to a capture CSV.
        params: optional simulation parameters (gains, airframe, etc.).
        dt: simulation timestep (default 500 Hz).
        store: Store to save the Run to. Defaults to a transient Store.
        kind: Run kind (default 'validation').
        tags: additional tags for the Run.

    Returns:
        The produced Run object (also stored if a Store is provided).
    """
    params = params or {}
    plant = IdentifiedPlant(dt=dt)
    pid = CascadedPID(dt=dt)
    ref_models: dict[str, ReferenceModel] = {}
    for axis in ("roll", "pitch", "yaw"):
        ref_models[axis] = ReferenceModel.for_axis(axis, dt=dt)

    # Extract trajectory from workflow or use directly
    trajectory = _extract_trajectory(workflow_or_trajectory)
    # Extract intent name from workflow if present
    intent_str = str(workflow_or_trajectory)[:200] if isinstance(workflow_or_trajectory, str) else ""
    if isinstance(workflow_or_trajectory, dict) and "name" in workflow_or_trajectory:
        intent_str = str(workflow_or_trajectory["name"])[:200]
    elif workflow_or_trajectory:
        intent_str = str(workflow_or_trajectory)[:200]
    if not trajectory:
        run = Run(
            kind=kind,
            tags=(tags or []) + ["sim"],
            params=params,
            outcome="skipped",
            metrics={},
        )
        if store:
            store.create(run)
        return run

    # Build replay with zero adaptive correction (PID-only = xm_physics)
    replay = Replay(plant=plant, pid=pid, ref_models=ref_models, dt=dt)

    # Run the simulation
    result = replay.run(trajectory)

    # Build metrics from replay result
    metrics: dict[str, Any] = {}
    for axis in ("roll", "pitch", "yaw"):
        response = result.response.get(axis, [])
        xm = result.xm_physics.get(axis, [])
        if response and xm:
            # RMSE of tracking error between plant response and xm_physics
            errors = [r - x for r, x in zip(response, xm)]
            rmse_val = _rmse(errors)
            metrics[f"{axis}_rmse"] = rmse_val
            metrics[f"{axis}_n_samples"] = len(response)

    # Determine outcome
    outcome = "pass"
    if not result.t:
        outcome = "no_data"
    elif all(result.response.get(axis) is None
             for axis in ("roll", "pitch", "yaw")):
        outcome = "error"

    run = Run(
        kind=kind,
        intent=intent_str,
        tags=(tags or []) + ["sim"],
        params=params,
        outcome=outcome,
        metrics=metrics,
    )

    # Save captures and run
    if store:
        store.create(run)
        # Save replay data as capture CSV
        _save_capture_csv(store, run.id, result, dt)

    return run


def _extract_trajectory(workflow_or_trajectory: Any) -> list[dict[str, Any]]:
    """Extract a list of capture dicts from the workflow input."""
    if isinstance(workflow_or_trajectory, str):
        path = Path(workflow_or_trajectory)
        if path.exists():
            return _load_csv_trajectory(str(path))
        return []
    if isinstance(workflow_or_trajectory, dict):
        if "trajectory" in workflow_or_trajectory:
            return _extract_trajectory(workflow_or_trajectory["trajectory"])
        if "t" in workflow_or_trajectory:
            return [workflow_or_trajectory]
        return []
    if isinstance(workflow_or_trajectory, (list, tuple)):
        return list(workflow_or_trajectory)
    return []


def _load_csv_trajectory(path: str) -> list[dict[str, Any]]:
    """Load a trajectory from a CSV file."""
    import csv as csv_mod
    captures: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv_mod.DictReader(fh)
        for row in reader:
            entry: dict[str, Any] = {}
            for k, v in row.items():
                try:
                    entry[k] = float(v)
                except (ValueError, TypeError):
                    entry[k] = v
            captures.append(entry)
    return captures


def _save_capture_csv(store: Store, run_id: str,
                      result: ReplayResult, dt: float) -> None:
    """Save replay time-series as a CSV in the run's captures/ directory."""
    import csv as csv_mod
    import io

    axes = ("roll", "pitch", "yaw")
    if not result.t:
        return

    output = io.StringIO()
    writer = csv_mod.writer(output)
    header = ["t"]
    for axis in axes:
        header += [f"{axis}_sp", f"{axis}_response", f"{axis}_xm_physics",
                   f"{axis}_pid_output", f"{axis}_u_ad"]
    writer.writerow(header)

    n = len(result.t)
    for i in range(n):
        row = [result.t[i]]
        for axis in axes:
            sp = result.setpoint.get(axis, [0.0] * n)
            resp = result.response.get(axis, [0.0] * n)
            xm = result.xm_physics.get(axis, [0.0] * n)
            po = result.pid_output.get(axis, [0.0] * n)
            ua = result.u_ad.get(axis, [0.0] * n)
            row += [sp[i] if i < len(sp) else 0.0,
                    resp[i] if i < len(resp) else 0.0,
                    xm[i] if i < len(xm) else 0.0,
                    po[i] if i < len(po) else 0.0,
                    ua[i] if i < len(ua) else 0.0]
        writer.writerow(row)

    # Write the CSV to the store's runs directory
    run_dir = Path(store.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cap_dir = run_dir / "captures"
    cap_dir.mkdir(exist_ok=True)
    cap_path = cap_dir / "replay.csv"
    cap_path.write_text(output.getvalue(), encoding="utf-8")


def _rmse(errors: list[float]) -> float:
    """Root mean square of errors."""
    if not errors:
        return 0.0
    return (sum(e * e for e in errors) / len(errors)) ** 0.5
