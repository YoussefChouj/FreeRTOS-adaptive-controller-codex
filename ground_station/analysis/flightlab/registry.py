"""Plugin and rule registries (spec sections 6 and 7).

Owned by the supervisor (WP0). Workers must not edit this file.

Plugin modules live in flightlab/plugins/, rule modules in flightlab/rules/. The pipeline imports
every module in both packages, so a new plugin or rule is a new file; no core change.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

SEVERITIES = ("critical", "warn", "info")          # sort order of recommendations.json
CATEGORIES = ("data", "pid", "mrac", "hardware", "battery", "logging")
ACTIONS = ("increase", "decrease", "investigate", "add_to_preset", "enable", "disable")
CONFIDENCES = ("low", "medium", "high")


@runtime_checkable
class Plugin(Protocol):
    name: str      # key in metrics.json
    order: int     # run order, lower first (optional attribute, default 100)

    def requires(self, log, cfg: dict) -> list[str]: ...          # missing var names; [] = can run
    def run(self, log, segs: dict, cfg: dict) -> dict: ...       # JSON-serialisable, floats or None only
    def figures(self, log, segs: dict, cfg: dict, out_dir: Path) -> list[Path]: ...


PLUGINS: dict[str, Any] = {}


def register_plugin(cls):
    """Class decorator: instantiate with no arguments and register under cls.name."""
    inst = cls()
    name = getattr(inst, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError(f"{cls.__name__}: plugin needs a non-empty str 'name'")
    for meth in ("requires", "run", "figures"):
        if not callable(getattr(inst, meth, None)):
            raise ValueError(f"plugin {name}: missing method {meth}()")
    if name in PLUGINS and type(PLUGINS[name]).__qualname__ != cls.__qualname__:
        raise ValueError(f"plugin name {name!r} registered twice")
    PLUGINS[name] = inst
    return cls


def plugin_order(p) -> tuple[int, str]:
    return (int(getattr(p, "order", 100)), p.name)


@dataclass
class Recommendation:
    id: str                 # e.g. "PID-OSC-rate_roll"
    severity: str           # "info" | "warn" | "critical"
    category: str           # "data" | "pid" | "mrac" | "hardware" | "battery" | "logging"
    target: str | None      # firmware field, e.g. "Ctrler.gyroxPID.Kd"
    action: str             # "increase" | "decrease" | "investigate" | "add_to_preset" | "enable" | "disable"
    factor: float | None    # suggested multiplier, e.g. 0.85
    evidence: dict          # {metric json-path: value} - every number the rule read
    rationale: str          # fixed template string filled with evidence values
    confidence: str         # "low" | "medium" | "high"

    def __post_init__(self):
        for fld, allowed in (("severity", SEVERITIES), ("category", CATEGORIES),
                             ("action", ACTIONS), ("confidence", CONFIDENCES)):
            if getattr(self, fld) not in allowed:
                raise ValueError(f"{self.id}: {fld}={getattr(self, fld)!r} not in {allowed}")
        if not isinstance(self.evidence, dict):
            raise ValueError(f"{self.id}: evidence must be a dict")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleEntry:
    name: str
    fn: Callable[[dict, dict, dict], list]
    requires: list[str]


RULES: dict[str, RuleEntry] = {}


def register_rule(requires: list[str]):
    """Decorator for fn(metrics, cfg, ctx) -> list[Recommendation].

    `requires` holds dotted metric paths (e.g. "loops", "motors.airborne.clamp_hi_frac"). The rule is
    skipped when any of them is missing or None. ctx = {"ledger_rows": list[dict], "log": FlightLog}.
    """
    def deco(fn):
        RULES[fn.__name__] = RuleEntry(fn.__name__, fn, list(requires))
        return fn
    return deco


def get_path(d: Any, path: str) -> Any:
    """Dotted lookup into nested dicts; None when any step is missing."""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def sort_recommendations(recs: list[Recommendation]) -> list[Recommendation]:
    return sorted(recs, key=lambda r: (SEVERITIES.index(r.severity), r.id))
