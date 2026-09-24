"""Workflow schema, loader and validator.

A workflow is a sequence of *typed steps* composed into a version-controlled
experiment plan.  Each step declares its type and a payload; the loader parses
YAML, the validator checks completeness and type safety.

Known step types:
    ``set_params``  – write a dict of parameters (goes through the service's
                      action registry as a ``command`` step or ``subscribe``).
    ``fly_trajectory``  – fly a trajectory (produces a ``fly_trajectory`` plan
                          step referencing the trajectory name).
    ``capture``           – start a capture (uses the livewatch capture preset).
    ``wait_until``        – wait until a telemetry predicate holds
                            (uses the service ``wait_for`` action).
    ``analyze``           – run the T2 analysis pipeline on the latest captures.
    ``revert``            – restore parameters to their pre-experiment values.
    ``note``              – append a human-readable note to the Run.
    ``call``              – compose another workflow by name (recursive).

Unknown step types are a validation error.

An experiment spec carries:
    hypothesis, phase, variant, changes, trajectory, capture,
    envelope (state bounds + adaptive-health bounds, mode: ``enforce`` or
    ``observe_only``), and ``revert: always``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# ---------------------------------------------------------------------------
# Known step types (the canonical set)
# ---------------------------------------------------------------------------

KNOWN_STEP_TYPES: frozenset[str] = frozenset((
    "set_params",
    "fly_trajectory",
    "capture",
    "wait_until",
    "analyze",
    "revert",
    "note",
    "call",
))

VALID_ENVELOPE_MODES: frozenset[str] = frozenset(("enforce", "observe_only"))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvelopeBound:
    """One bound inside the envelope section."""
    kind: str  # "state" or "adaptive_health"
    key: str   # e.g. "roll.max_deg", "w_norm.max"
    lo: float
    hi: float
    mode: str  # "enforce" or "observe_only"


@dataclass(frozen=True)
class WorkflowSpec:
    """A validated workflow specification."""
    name: str
    hypothesis: str = ""
    phase: str = ""
    variant: str = ""
    changes: list[str] = field(default_factory=list)
    trajectory: str = ""
    capture: Optional[dict[str, Any]] = None
    envelope: Optional[list[EnvelopeBound]] = None
    revert: str = "always"
    steps: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_enforce(self) -> bool:
        """True when at least one envelope bound uses ``enforce`` mode."""
        if not self.envelope:
            return False
        return any(b.mode == "enforce" for b in self.envelope)

    @property
    def has_observation_only(self) -> bool:
        """True when at least one envelope bound uses ``observe_only`` mode."""
        if not self.envelope:
            return False
        return any(b.mode == "observe_only" for b in self.envelope)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "hypothesis": self.hypothesis,
            "phase": self.phase,
            "variant": self.variant,
            "changes": self.changes,
            "trajectory": self.trajectory,
            "capture": self.capture,
            "envelope": [
                {"kind": b.kind, "key": b.key, "lo": b.lo, "hi": b.hi, "mode": b.mode}
                for b in self.envelope
            ] if self.envelope else None,
            "revert": self.revert,
            "steps": self.steps,
        }
        return d


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_workflow(path: str | Path) -> dict[str, Any]:
    """Load and return the raw workflow dict from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"workflow not found: {p}")
    with open(p, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"workflow file must contain a mapping, got {type(data).__name__}")
    return data


def _parse_envelope(raw_envelope: Any) -> list[EnvelopeBound]:
    """Parse the envelope section of a workflow."""
    if raw_envelope is None:
        return []
    if not isinstance(raw_envelope, list):
        raise ValueError(f"envelope must be a list, got {type(raw_envelope).__name__}")
    bounds: list[EnvelopeBound] = []
    for item in raw_envelope:
        if not isinstance(item, dict):
            raise ValueError(f"each envelope bound must be a mapping, got {type(item).__name__}")
        kind = item.get("kind")
        key = item.get("key")
        lo = item.get("lo")
        hi = item.get("hi")
        mode = item.get("mode")
        if not all(v is not None for v in (kind, key, lo, hi, mode)):
            raise ValueError(
                f"envelope bound requires keys: kind, key, lo, hi, mode"
                f" (got {sorted(item.keys())})"
            )
        if kind not in ("state", "adaptive_health"):
            raise ValueError(f"envelope bound kind must be 'state' or 'adaptive_health', got {kind!r}")
        if mode not in VALID_ENVELOPE_MODES:
            raise ValueError(
                f"envelope bound mode must be one of {VALID_ENVELOPE_MODES}, got {mode!r}"
            )
        try:
            lo = float(lo)
            hi = float(hi)
        except (TypeError, ValueError):
            raise ValueError(f"envelope bound lo/hi must be numeric")
        bounds.append(EnvelopeBound(kind=kind, key=key, lo=lo, hi=hi, mode=mode))
    return bounds


def validate_workflow(raw: dict[str, Any], *, source_path: str = "") -> WorkflowSpec:
    """Parse and validate a raw workflow dict into a ``WorkflowSpec``.

    Raises ``ValueError`` when the workflow is invalid.
    """
    if not isinstance(raw, dict):
        raise ValueError("workflow must be a YAML mapping")

    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("workflow requires a string 'name' field")

    # --- Steps --------------------------------------------------------
    raw_steps = raw.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError("workflow 'steps' must be a list")
    if not raw_steps:
        raise ValueError("workflow 'steps' must not be empty")

    parsed_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            raise ValueError(f"step[{idx}] must be a mapping")
        step_type = step.get("type")
        if not step_type:
            raise ValueError(f"step[{idx}] missing 'type'")
        if step_type not in KNOWN_STEP_TYPES:
            raise ValueError(
                f"step[{idx}]: unknown step type {step_type!r}; "
                f"known types: {sorted(KNOWN_STEP_TYPES)}"
            )
        # Each step must carry at least the 'type'; extras are passthrough.
        parsed_steps.append({**step, "_index": idx})

    # --- Envelope -----------------------------------------------------
    envelope = _parse_envelope(raw.get("envelope"))

    # --- Capture (optional) -------------------------------------------
    cap = raw.get("capture")
    if cap is not None and not isinstance(cap, dict):
        raise ValueError("workflow 'capture' must be a mapping or absent")

    return WorkflowSpec(
        name=str(name),
        hypothesis=str(raw.get("hypothesis", "")),
        phase=str(raw.get("phase", "")),
        variant=str(raw.get("variant", "")),
        changes=raw.get("changes", []),
        trajectory=str(raw.get("trajectory", "")),
        capture=cap,
        envelope=envelope if envelope else None,
        revert=str(raw.get("revert", "always")),
        steps=parsed_steps,
        source_path=source_path,
        raw=raw,
    )


def validate_workflow_file(path: str | Path) -> WorkflowSpec:
    """Load a YAML file and validate it."""
    raw = load_workflow(path)
    return validate_workflow(raw, source_path=str(path))


# ---------------------------------------------------------------------------
# Schema helpers for dry-run / dry validation
# ---------------------------------------------------------------------------

def collect_step_types(spec: WorkflowSpec) -> set[str]:
    """Return the set of distinct step types in the workflow."""
    return {s.get("type") for s in spec.steps}


def step_params(step: dict[str, Any]) -> dict[str, Any]:
    """Return the payload of a step (everything except 'type' and '_index')."""
    return {k: v for k, v in step.items() if k not in ("type", "_index")}
