"""Campaign planner: operator-approved envelope over parameters.

An operator approves an *envelope* (parameter ranges + a step budget).
The campaign proposes the next point (grid search first, then successive
halving as an option), stays inside the envelope, and stops when the budget
is exhausted.

Level 3 autonomy: the operator approves the envelope once, then the agent
picks points inside it autonomously.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Envelope and point representations
# ---------------------------------------------------------------------------

@dataclass
class ParamRange:
    """A parameter with its allowed range for the campaign."""
    name: str
    lo: float
    hi: float
    steps: int = 10  # discretisation resolution within the range


@dataclass
class CampaignEnvelope:
    """Operator-approved parameter ranges."""
    params: dict[str, ParamRange] = field(default_factory=dict)
    mode: str = "observe_only"  # "enforce" or "observe_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": {k: {"lo": v.lo, "hi": v.hi, "steps": v.steps}
                       for k, v in self.params.items()},
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignEnvelope:
        params: dict[str, ParamRange] = {}
        for name, spec in data.get("params", {}).items():
            params[name] = ParamRange(
                name=name,
                lo=float(spec["lo"]),
                hi=float(spec["hi"]),
                steps=int(spec.get("steps", 10)),
            )
        return cls(params=params, mode=data.get("mode", "observe_only"))


@dataclass
class Point:
    """A candidate parameter point."""
    params: dict[str, float]
    score: Optional[float] = None  # result from the last run

    def to_dict(self) -> dict[str, Any]:
        d = {"params": dict(self.params)}
        if self.score is not None:
            d["score"] = self.score
        return d


@dataclass
class CampaignPlan:
    """The plan for one campaign run."""
    envelope: CampaignEnvelope
    budget_steps: int
    strategy: str = "grid"  # "grid" or "successive_halving"
    points: list[Point] = field(default_factory=list)
    current_index: int = 0
    completed: bool = False
    remaining_budget: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_dict(),
            "budget_steps": self.budget_steps,
            "strategy": self.strategy,
            "current_index": self.current_index,
            "completed": self.completed,
            "remaining_budget": self.remaining_budget,
            "points_total": len(self.points),
            "points_run": self.current_index,
        }


# ---------------------------------------------------------------------------
# Point proposal
# ---------------------------------------------------------------------------

def propose_grid_points(envelope: CampaignEnvelope) -> list[Point]:
    """Generate a full grid search over the envelope parameters.

    This can be a very large number of points.  In practice the operator
    constrains the number of parameter dimensions or uses a fractional grid.
    For this implementation, we generate a small default grid (3 points
    per parameter) and cap the total at 100.
    """
    param_names = sorted(envelope.params.keys())
    if not param_names:
        return []

    points_per_dim = min(3, max(2, min(
        envelope.params[p].steps for p in param_names
    )))

    # Build coordinate ranges
    coords: list[list[float]] = []
    for name in param_names:
        pr = envelope.params[name]
        step_size = (pr.hi - pr.lo) / max(1, points_per_dim - 1)
        if step_size == 0:
            coords.append([pr.lo])
        else:
            coords.append([pr.lo + i * step_size for i in range(points_per_dim)])

    # Generate grid points, cap at 100
    from itertools import product
    grid: list[Point] = []
    for combo in product(*coords):
        params = dict(zip(param_names, combo))
        grid.append(Point(params=params))
        if len(grid) >= 100:
            break

    return grid


def propose_next_halving(
    envelope: CampaignEnvelope,
    previous_points: list[Point],
    n_candidates: int = 4,
) -> list[Point]:
    """Propose new points using successive halving.

    Picks points near the best-performing previous point plus perturbations.
    """
    if not previous_points:
        # First round: random sample from grid
        return propose_grid_points(envelope)[:n_candidates]

    # Find the best scored point
    scored = [p for p in previous_points if p.score is not None]
    if not scored:
        return propose_grid_points(envelope)[:n_candidates]

    best = max(scored, key=lambda p: p.score or 0)
    param_names = sorted(envelope.params.keys())

    candidates: list[Point] = []
    for name in param_names:
        pr = envelope.params[name]
        spread = (pr.hi - pr.lo) * 0.2  # 20% perturbation
        for sign in (-1, 1):
            p = Point(params=dict(best.params))
            val = p.params.get(name, pr.lo)
            new_val = max(pr.lo, min(pr.hi, val + sign * spread))
            p.params[name] = new_val
            if p not in candidates:
                candidates.append(p)
            if len(candidates) >= n_candidates:
                break
        if len(candidates) >= n_candidates:
            break

    # Ensure all candidates are within envelope
    return [c for c in candidates if _is_in_envelope(c, envelope)]


def _is_in_envelope(point: Point, envelope: CampaignEnvelope) -> bool:
    """Check if a point is within the campaign envelope."""
    for name, pr in envelope.params.items():
        val = point.params.get(name)
        if val is None:
            continue
        if val < pr.lo or val > pr.hi:
            return False
    return True


# ---------------------------------------------------------------------------
# Campaign engine
# ---------------------------------------------------------------------------

def plan_campaign(
    envelope: CampaignEnvelope,
    budget_steps: int,
    strategy: str = "grid",
) -> CampaignPlan:
    """Create a campaign plan from an envelope.

    Args:
        envelope: operator-approved parameter ranges.
        budget_steps: maximum number of experiment steps.
        strategy: "grid" (default) or "successive_halving".

    Returns:
        A ``CampaignPlan`` ready to execute.
    """
    plan = CampaignPlan(
        envelope=envelope,
        budget_steps=budget_steps,
        strategy=strategy,
        remaining_budget=budget_steps,
    )

    if strategy == "grid":
        plan.points = propose_grid_points(envelope)
    else:
        # successive_halving starts with a grid then iterates
        plan.points = propose_grid_points(envelope)
        if len(plan.points) > budget_steps:
            plan.points = plan.points[:budget_steps]

    return plan


def next_point(plan: CampaignPlan) -> Optional[Point]:
    """Return the next point to run, or None if budget is exhausted."""
    if plan.completed or plan.current_index >= len(plan.points):
        return None
    if plan.remaining_budget <= 0:
        plan.completed = True
        return None
    point = plan.points[plan.current_index]
    plan.current_index += 1
    plan.remaining_budget -= 1
    return point


def record_result(plan: CampaignPlan, point_index: int, score: float) -> None:
    """Record the score for a point so successive_halving can pick smarter."""
    if 0 <= point_index < len(plan.points):
        plan.points[point_index].score = score

    # If strategy is successive_halving, generate new candidates
    if plan.strategy == "successive_halving" and plan.remaining_budget > 0:
        previous = plan.points[:plan.current_index]
        new_points = propose_next_halving(plan.envelope, previous)
        for np_ in new_points:
            if len(plan.points) < plan.budget_steps + plan.current_index:
                plan.points.append(np_)


def campaign_is_within_envelope(
    point: Point, envelope: CampaignEnvelope
) -> bool:
    """True when every parameter in *point* is inside the envelope."""
    return _is_in_envelope(point, envelope)


def campaign_budget_exhausted(plan: CampaignPlan) -> bool:
    """True when the budget has been consumed."""
    return plan.remaining_budget <= 0 or plan.current_index >= len(plan.points)
