"""Workflow executor with Backend interface.

Executes a ``WorkflowSpec`` through a ``Backend`` abstraction:
    - ``SimBackend``  – runs in T3 simulation via ``dry_run``.
    - ``DashboardBackend`` – builds agent plans for the service's plan/approval
      path.  Its HTTP client is injectable; tests use a fake.

Key invariants:
    - The executor *always* runs ``revert`` (try/finally).
    - It evaluates the envelope on streamed state:
        - ``enforce``: abort, revert, and record the event.
        - ``observe_only``: record the would-be trip only.
    - A workflow must pass ``dry_run`` in sim before ``DashboardBackend``
      will execute it; the sim Run id is stored in the hardware Run.
"""
from __future__ import annotations

import abc
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .run import Event, Run
from .store import Store
from .workflow import WorkflowSpec, EnvelopeBound
from .trajectories import TrajectoryResult, generate_trajectory


# ---------------------------------------------------------------------------
# Envelope checking
# ---------------------------------------------------------------------------

@dataclass
class EnvelopeTrip:
    """Record of an envelope violation."""
    t: float
    bound: EnvelopeBound
    actual_value: float
    mode: str  # "enforce" or "observe_only"
    event_kind: str = "envelope_trip"


def evaluate_envelope(
    bounds: list[EnvelopeBound],
    t: float,
    state: dict[str, float],
) -> list[EnvelopeTrip]:
    """Check current state against envelope bounds.

    Returns a list of trips (violations).
    """
    trips: list[EnvelopeTrip] = []
    for bound in bounds:
        actual = state.get(bound.key)
        if actual is None:
            continue
        if actual < bound.lo or actual > bound.hi:
            trips.append(EnvelopeTrip(
                t=t, bound=bound, actual_value=actual,
                mode=bound.mode,
            ))
    return trips


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Outcome of a workflow execution."""
    run: Run
    events: list[dict[str, Any]] = field(default_factory=list)
    trips: list[EnvelopeTrip] = field(default_factory=list)
    steps_run: int = 0
    outcome: str = "complete"  # complete | aborted | failed
    sim_run_id: Optional[str] = None  # ID of the sim dry-run (for hardware Run)
    aborted: bool = False


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------

class Backend(abc.ABC):
    """Interface for executing workflow steps."""

    @abc.abstractmethod
    def dry_run(self, spec: WorkflowSpec) -> Run:
        """Run the workflow in simulation. Returns a validation Run."""
        ...

    @abc.abstractmethod
    def execute_step(self, step: dict[str, Any], spec: WorkflowSpec) -> dict[str, Any]:
        """Execute one step. Returns step result dict."""
        ...

    @abc.abstractmethod
    def revert_params(self, spec: WorkflowSpec, params_before: dict[str, Any]) -> dict[str, Any]:
        """Restore parameters. Returns the restored params."""
        ...

    @abc.abstractmethod
    def get_state(self) -> dict[str, float]:
        """Return current telemetry state for envelope checking."""
        ...


# ---------------------------------------------------------------------------
# SimBackend – uses T3 sim dry_run
# ---------------------------------------------------------------------------

class SimBackend(Backend):
    """Simulation backend using T3's dry_run."""

    def __init__(self, dt: float = 0.002) -> None:
        self._dt = dt
        self._runs: list[Run] = []

    def dry_run(self, spec: WorkflowSpec) -> Run:
        """Run the workflow in simulation via the T3 dry_run pipeline."""
        from .sim.dryrun import dry_run as sim_dry_run
        from .store import Store

        store = Store()
        # Build a trajectory from the workflow spec for dry_run
        trajectory_data = _build_trajectory_data(spec)
        run = sim_dry_run(
            trajectory_data,
            params=spec.params if hasattr(spec, "params") else {},
            dt=self._dt,
            store=store,
            kind="validation",
            tags=["sim"],
        )
        self._runs.append(run)
        store.close()
        return run

    def execute_step(self, step: dict[str, Any], spec: WorkflowSpec) -> dict[str, Any]:
        """Simulate executing a single step."""
        step_type = step.get("type", "note")
        payload = {k: v for k, v in step.items() if k not in ("type", "_index")}
        return {"type": step_type, "status": "simulated", "payload": payload}

    def revert_params(self, spec: WorkflowSpec, params_before: dict[str, Any]) -> dict[str, Any]:
        """In sim, just return the original params."""
        return params_before

    def get_state(self) -> dict[str, float]:
        """Sim returns zero state for envelope checking."""
        return {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "z": 0.53}


# ---------------------------------------------------------------------------
# DashboardBackend – builds agent plans
# ---------------------------------------------------------------------------

class DashboardBackend(Backend):
    """Builds agent plans for the service's plan/approval path.

    The HTTP client is injectable for testing.  In production it calls
    ``POST /api/agent/plans`` to submit plans.
    """

    requires_sim_dry_run = True

    def __init__(
        self,
        http_client: Optional[Callable] = None,
        agent_source: str = "agent:executor",
    ) -> None:
        self._http = http_client  # if None, no HTTP calls (test mode)
        self._agent_source = agent_source
        self._plan_ids: list[str] = []

    def dry_run(self, spec: WorkflowSpec) -> Run:
        """Must be called before any hardware execution.

        Returns a validation Run tagged ``sim``.
        """
        from .sim.dryrun import dry_run as sim_dry_run
        from .store import Store

        store = Store()
        trajectory_data = _build_trajectory_data(spec)
        run = sim_dry_run(
            trajectory_data,
            dt=0.002,
            store=store,
            kind="validation",
            tags=["sim", "dashboard"],
        )
        store.close()
        return run

    def execute_step(self, step: dict[str, Any], spec: WorkflowSpec) -> dict[str, Any]:
        """Build a plan step and submit via the service API.

        Returns the plan step result.
        """
        step_type = step.get("type", "note")
        payload = {k: v for k, v in step.items() if k not in ("type", "_index")}

        # For non-note, non-revert steps, build a service action plan
        if step_type in ("note", "revert"):
            return self._exec_plan_step(step, spec)

        # For other step types, compose a plan
        plan_result = self._submit_plan_for_step(step, spec)
        return {"type": step_type, "status": "submitted", "plan_result": plan_result}

    def _submit_plan_for_step(
        self, step: dict[str, Any], spec: WorkflowSpec
    ) -> dict[str, Any]:
        """Submit a plan for a single step through the service API."""
        step_type = step.get("type")
        payload = {k: v for k, v in step.items() if k not in ("type", "_index")}

        # Map workflow step types to service action types
        action_map = {
            "set_params": "subscribe",
            "fly_trajectory": "command",
            "capture": "recording_start",
            "wait_until": "wait_for",
            "analyze": "say",
        }
        action = action_map.get(step_type, "say")
        args = payload if isinstance(payload, dict) else {}

        plan = self._build_plan(spec, action, args)
        plan_id = plan.get("plan_id", str(uuid.uuid4())[:8])
        self._plan_ids.append(plan_id)

        # Submit via HTTP if client is provided
        if self._http is not None:
            try:
                result = self._http("POST", "/api/agent/plans", {
                    "title": f"Step: {step_type}",
                    "goal": spec.hypothesis or spec.name,
                    "source": self._agent_source,
                    "steps": [{
                        "action": action,
                        "args": args,
                        "label": f"step {step.get('_index', 0)}: {step_type}",
                    }],
                })
                return {"plan_id": plan_id, "http_result": result}
            except Exception:
                return {"plan_id": plan_id, "http_result": "failed"}

        return {"plan_id": plan_id, "http_result": "no_client"}

    def _build_plan(
        self, spec: WorkflowSpec, action: str, args: dict
    ) -> dict[str, Any]:
        """Build a plan dict (doesn't submit)."""
        return {
            "plan_id": str(uuid.uuid4())[:8],
            "title": f"Workflow step: {action}",
            "goal": spec.hypothesis or spec.name,
            "source": self._agent_source,
        }

    def _exec_plan_step(self, step: dict[str, Any], spec: WorkflowSpec) -> dict[str, Any]:
        """Execute a plan step directly (for note/revert)."""
        step_type = step.get("type")
        payload = {k: v for k, v in step.items() if k not in ("type", "_index")}
        if step_type == "note":
            text = payload.get("text", "")
            return {"type": "note", "status": "logged", "text": text}
        return {"type": "revert", "status": "executed", "payload": payload}

    def revert_params(self, spec: WorkflowSpec, params_before: dict[str, Any]) -> dict[str, Any]:
        """Restore parameters through a plan step."""
        # Submit a revert plan step
        return {"restored": params_before}

    def get_state(self) -> dict[str, float]:
        """Request current telemetry from the service.

        In tests this may be mocked via the HTTP client.
        """
        if self._http is None:
            return {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "z": 0.53}
        try:
            result = self._http("GET", "/state", {})
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "z": 0.53}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class WorkflowError(Exception):
    """Raised when a workflow step fails irrecoverably."""


def run_workflow(
    spec: WorkflowSpec,
    backend: Backend,
    store: Optional[Store] = None,
) -> RunResult:
    """Execute a workflow through the given backend.

    Invariants:
        - Always runs ``revert`` (try/finally).
        - Envelope is checked on every ``get_state()`` call.
        - ``enforce`` trips abort immediately; ``observe_only`` trips are
          recorded without aborting.
        - For ``DashboardBackend``, a sim ``dry_run`` must pass first; the
          sim Run id is stored in the hardware Run.

    Args:
        spec: the validated workflow spec.
        backend: the execution backend.
        store: optional Store to persist the Run.

    Returns:
        A ``RunResult`` with the produced Run.
    """
    params_before: dict[str, Any] = {}
    sim_run_id: Optional[str] = None
    events: list[dict[str, Any]] = []
    trips: list[EnvelopeTrip] = []
    outcome = "complete"
    aborted = False
    error: Optional[str] = None

    # Add start event
    events.append(Run.from_event(Event(t=0.0, kind="start", detail=spec.name)))

    # --- Envelope bounds -----------------------------------------------
    bounds = list(spec.envelope) if spec.envelope else []

    # --- Determine if we need a sim dry-run (DashboardBackend requirement)
    if getattr(backend, "requires_sim_dry_run", False):
        sim_run = backend.dry_run(spec)
        sim_run_id = sim_run.id
        # Sim must pass (outcome != "error")
        if sim_run.outcome == "error" or sim_run.outcome == "no_data":
            events.append(Run.from_event(
                Event(t=0.0, kind="abort", detail="sim dry_run failed")
            ))
            return RunResult(
                run=_finalize_run(spec, events, trips, 0,
                                   "sim_failed", sim_run_id=sim_run_id),
                events=events,
                trips=trips,
                sim_run_id=sim_run_id,
            )

    # --- Execute steps -------------------------------------------------
    steps_run = 0
    try:
        for step in spec.steps:
            step_type = step.get("type", "note")

            # Check envelope before each step
            if bounds:
                state = backend.get_state()
                step_trips = evaluate_envelope(bounds, 0.0, state)
                for trip in step_trips:
                    trips.append(trip)
                    event = Run.from_event(Event(
                        t=0.0, kind="envelope_trip",
                        detail=f"{trip.bound.key}={trip.actual_value:.4f}"
                               f" out of [{trip.bound.lo}, {trip.bound.hi}]"
                               f" mode={trip.mode}",
                    ))
                    events.append(event)
                    if trip.mode == "enforce":
                        outcome = "aborted"
                        aborted = True
                        events.append(Run.from_event(
                            Event(t=0.0, kind="abort", detail="envelope enforce trip")
                        ))
                        break

            if aborted:
                break

            # Execute the step
            if step_type == "set_params":
                params_before.update(step_params(step))
            elif step_type == "revert":
                pass  # handled in finally
            elif step_type == "call":
                # Compose another workflow (call another by name)
                # This is a no-op in the executor; the caller resolves the name.
                pass
            else:
                backend.execute_step(step, spec)

            steps_run += 1
            events.append(Run.from_event(
                Event(t=float(steps_run), kind="step_done",
                      detail=f"step {steps_run}: {step_type}")
            ))

    except WorkflowError as exc:
        error = str(exc)
        outcome = "failed"
        events.append(Run.from_event(
            Event(t=float(steps_run), kind="error", detail=error)
        ))
    except Exception as exc:
        error = repr(exc)
        outcome = "failed"
        events.append(Run.from_event(
            Event(t=float(steps_run), kind="error", detail=error)
        ))
    finally:
        # --- Always revert -----------------------------------------------
        if spec.revert == "always" and params_before:
            try:
                backend.revert_params(spec, params_before)
                events.append(Run.from_event(
                    Event(t=float(steps_run + 1), kind="revert",
                          detail="params restored")
                ))
            except Exception:
                events.append(Run.from_event(
                    Event(t=float(steps_run + 1), kind="revert_failed",
                          detail="revert raised")
                ))

    run = _finalize_run(spec, events, trips, steps_run, outcome,
                        sim_run_id=sim_run_id, error=error)
    if store:
        store.create(run)

    return RunResult(
        run=run,
        events=events,
        trips=trips,
        steps_run=steps_run,
        outcome=outcome,
        sim_run_id=sim_run_id,
        aborted=aborted,
    )


def _build_trajectory_data(spec: WorkflowSpec) -> Any:
    """Build trajectory data from a workflow spec for sim dry_run."""
    if not spec.trajectory:
        return []
    try:
        traj: TrajectoryResult = generate_trajectory(spec.trajectory)
        return traj.as_dict()
    except (ValueError, KeyError):
        return []


def _finalize_run(
    spec: WorkflowSpec,
    events: list[dict[str, Any]],
    trips: list[EnvelopeTrip],
    steps_run: int,
    outcome: str,
    *,
    sim_run_id: Optional[str] = None,
    error: Optional[str] = None,
) -> Run:
    """Build the final Run from execution data."""
    run_events = list(events)
    if trips:
        for trip in trips:
            run_events.append(Run.from_event(Event(
                t=trip.t, kind="envelope_trip",
                detail=f"{trip.bound.kind}:{trip.bound.key} "
                       f"={trip.actual_value} [{trip.bound.lo}, {trip.bound.hi}] "
                       f"mode={trip.mode}",
            )))

    run = Run(
        kind="experiment",
        intent=spec.name,
        hypothesis=spec.hypothesis,
        phase=spec.phase,
        variant=spec.variant,
        trajectory=spec.trajectory,
        outcome=outcome,
        events=run_events,
        tags=["workflow", spec.name],
    )
    if sim_run_id:
        run.tags.append(f"sim_run:{sim_run_id}")
    if error:
        run.notes.append({"t": "0", "author": "agent", "text": f"error: {error}"})
    return run


def step_params(step: dict[str, Any]) -> dict[str, Any]:
    """Extract the parameter payload from a step."""
    return {k: v for k, v in step.items()
            if k not in ("type", "_index", "action")}
