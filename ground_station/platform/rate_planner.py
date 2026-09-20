"""Multi-slot subscribe rate planner (WP5).

Pure computation. Given declarative slot requests (range count, total payload
bytes, transport, desired Hz) the planner chooses an integer Send_Task divider
for each slot, reports the *achieved* Hz (``cadence / divider`` — almost never
the desired Hz) and the wire occupancy per transport, and rejects plans that
break a structural limit or a transport wire budget. It does no hardware or
network I/O, sends no frame and needs no running service.

Every limit is imported from :mod:`ground_station.platform.firmware_contract`;
none is re-typed here.

Divider policy
--------------
Achievable rates are the discrete ladder ``cadence/1, cadence/2, cadence/3,
...`` — desired 30 Hz at a 100 Hz cadence can only be 33.3 Hz (divider 3) or
25 Hz (divider 4), never 30. This planner picks the **fastest ladder rung that
does not exceed the requested rate**, i.e. ``divider = ceil(cadence /
desired_hz)`` (30 Hz -> divider 4 -> 25 Hz). Rationale: the wire budget is the
binding constraint this planner exists to protect, so the default direction is
never to stream more than was asked for; a slot that wants the faster rung
pins ``divider`` explicitly, and the budget check then proves the overshoot
fits. A pinned
divider is always honoured as-is (its achieved-vs-desired gap is reported, not
auto-corrected).

Cadence — PARAMETER, not a constant (open hardware item)
--------------------------------------------------------
``cadence_hz`` defaults to the contract's ``SUBSCRIBE_SEND_TASK_HZ`` (100) but
the repo contradicts itself about the real Send_Task cadence; do not silently
"fix" one call site:

* ``ground_station/platform/firmware_contract.py:103`` —
  ``SUBSCRIBE_SEND_TASK_HZ = 100``, "(100 Hz by design)".
* ``ground_station/comm/manifest_layer.py:293`` — "10 Hz at 200 Hz Send_Task";
  ``ground_station/comm/manifest_layer.py:547`` prints ``200 // divider``.
* ``ground_station/comm/boot_default_layout.py:189`` — MIXED-mode *measured*
  cadence is ~80 Hz (200 Hz is called "nominal").
* The host-side stream module's ``SEND_TASK_HZ = 100`` (a fourth value, used
  for its budget guard) documents the same 200 nominal / ~80 MIXED split.

Resolving which cadence a powered drone actually runs in each mode needs the
hardware and belongs to the supervisor; all arithmetic here scales from
whatever cadence the caller passes.

Contract quirk (used as-is): ``frame_size_data_frame(n_ranges, total_bytes)``
ignores ``n_ranges`` (firmware_contract.py:126-132, returns
``10 + total_bytes + 2`` = ``SUBSCRIBE_STREAM_FRAME_OVERHEAD + total_bytes``).
It matches the documented firmware frame formula, so the planner does the same;
the range count is still validated against ``SUBSCRIBE_MAX_RANGES``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ground_station.platform.firmware_contract import (
    SUBSCRIBE_MAX_SLOTS,
    SUBSCRIBE_MAX_RANGES,
    SUBSCRIBE_STREAM_MAX_BYTES,
    SUBSCRIBE_STREAM_FRAME_OVERHEAD,
    SUBSCRIBE_SEND_TASK_HZ,
    SUBSCRIBE_BUDGET_PCT_USART3,
    SUBSCRIBE_BUDGET_PCT_UART5,
    frame_size_data_frame,
    effective_rate_hz,
    link_budget_usart3,
    link_budget_uart5,
)

# Wire-protocol transport ids (subscribe request byte). Protocol enum, not a
# budget limit — mirrors the comm stack's TRANSPORT_* ids and the contract's
# subscribe_validate_plan() (firmware_contract.py:593).
TRANSPORT_UART5 = 0
TRANSPORT_USART3 = 1

_TRANSPORT_NAMES = {
    TRANSPORT_UART5: "UART5",
    TRANSPORT_USART3: "USART3",
}
_BUDGET_PCT = {
    TRANSPORT_USART3: SUBSCRIBE_BUDGET_PCT_USART3,
    TRANSPORT_UART5: SUBSCRIBE_BUDGET_PCT_UART5,
}


@dataclass(frozen=True)
class SlotRequest:
    """One declarative slot request.

    Attributes:
        slot:        Slot index 0..SUBSCRIBE_MAX_SLOTS-1.
        n_ranges:    Number of (address, size, count) range tuples in the slot.
        total_bytes: Summed data payload width on the wire, bytes.
        transport:   ``TRANSPORT_USART3`` (1) or ``TRANSPORT_UART5`` (0).
        desired_hz:  Rate the caller wants for this slot, Hz (> 0).
        divider:     Optional pinned integer divider (>= 1). When ``None`` the
                     planner chooses one from ``desired_hz``. ``divider=0`` is
                     the firmware's documented STOP command
                     (manifest_layer.py:263) and is rejected — it is never a
                     rate.
    """

    slot: int
    n_ranges: int
    total_bytes: int
    transport: int
    desired_hz: float
    divider: Optional[int] = None


@dataclass(frozen=True)
class SlotPlan:
    """The planner's verdict for one slot."""

    slot: int
    transport: int
    n_ranges: int
    total_bytes: int
    frame_bytes: int
    divider: int
    desired_hz: float
    achieved_hz: float
    wire_pct: float  # this slot alone, share of its transport's wire

    @property
    def rate_gap_pct(self) -> float:
        """Achieved minus desired, as a percent of desired.

        Negative means the slot streams slower than requested; the ladder
        usually makes this nonzero.
        """
        return (self.achieved_hz - self.desired_hz) / self.desired_hz * 100.0


@dataclass(frozen=True)
class RatePlan:
    """Accepted multi-slot plan. Budgets are summed per transport, never
    across transports."""

    slots: tuple[SlotPlan, ...]
    cadence_hz: float
    transport_pct: dict[int, float] = field(default_factory=dict)
    transport_budget_pct: dict[int, int] = field(default_factory=dict)


class PlanRejected(Exception):
    """The plan is infeasible. Structural violations leave ``transport`` as
    ``None``; a budget violation names the binding transport, the percentage
    needed and the budget."""

    def __init__(self,
                 reasons: list[str],
                 transport: Optional[int] = None,
                 needed_pct: Optional[float] = None,
                 budget_pct: Optional[float] = None):
        self.reasons = list(reasons)
        self.transport = transport
        self.needed_pct = needed_pct
        self.budget_pct = budget_pct
        super().__init__("; ".join(reasons))


def choose_divider(desired_hz: float, cadence_hz: float = SUBSCRIBE_SEND_TASK_HZ) -> int:
    """Smallest integer divider whose achieved rate stays <= ``desired_hz``.

    ``divider = ceil(cadence / desired)`` clipped to >= 1. Desired 30 Hz at
    100 Hz cadence therefore gives divider 4 (25 Hz), never divider 3
    (33.3 Hz): see the module docstring for the conservative-ladder policy.
    Desired at/above cadence clips to divider 1 (the cadence is the ceiling).
    """
    if desired_hz <= 0:
        raise PlanRejected([f"desired_hz must be > 0, got {desired_hz}"])
    if cadence_hz <= 0:
        raise PlanRejected([f"cadence_hz must be > 0, got {cadence_hz}"])
    return max(1, math.ceil(cadence_hz / desired_hz - 1e-9))


def _wire_pct(transport: int, frame_bytes: int, divider: int,
              cadence_hz: float) -> float:
    """Share of the transport's wire in percent, scaled from the contract's
    100 Hz link-budget functions to the requested Send_Task cadence."""
    scale = cadence_hz / SUBSCRIBE_SEND_TASK_HZ
    if transport == TRANSPORT_USART3:
        return link_budget_usart3(frame_bytes, divider) * scale
    return link_budget_uart5(frame_bytes, divider) * scale


def plan_slots(requests: list[SlotRequest],
               cadence_hz: float = SUBSCRIBE_SEND_TASK_HZ
               ) -> RatePlan:
    """Plan up to ``SUBSCRIBE_MAX_SLOTS`` subscriptions on one Send_Task.

    Args:
        requests:    One :class:`SlotRequest` per occupied slot (1..4).
        cadence_hz:  Measured/nominal Send_Task cadence this plan is built
                     for. Defaults to ``SUBSCRIBE_SEND_TASK_HZ`` (100) — see
                     the module docstring for the 100/200/80 Hz conflict.

    Returns:
        A :class:`RatePlan` with divider, achieved Hz and wire percent per
        slot and total wire percent per transport.

    Raises:
        PlanRejected: A structural limit was broken (too many slots/ranges/
            bytes, bad slot/transport/divider) or a transport's budget was
            exceeded. A budget rejection names the transport, the percentage
            needed and the budget.
    """
    reasons: list[str] = []

    if not requests:
        raise PlanRejected(["plan contains no slots"])
    if len(requests) > SUBSCRIBE_MAX_SLOTS:
        reasons.append(
            f"{len(requests)} slots requested but the firmware supports at "
            f"most {SUBSCRIBE_MAX_SLOTS}"
        )

    seen_slots: set[int] = set()
    for req in requests:
        label = f"slot {req.slot}"
        if not 0 <= req.slot < SUBSCRIBE_MAX_SLOTS:
            reasons.append(
                f"{label}: slot index must be 0..{SUBSCRIBE_MAX_SLOTS - 1}"
            )
        elif req.slot in seen_slots:
            reasons.append(f"{label}: slot requested more than once")
        else:
            seen_slots.add(req.slot)
        if req.n_ranges < 1:
            reasons.append(f"{label}: n_ranges must be >= 1, got {req.n_ranges}")
        elif req.n_ranges > SUBSCRIBE_MAX_RANGES:
            reasons.append(
                f"{label}: n_ranges {req.n_ranges} exceeds "
                f"SUBSCRIBE_MAX_RANGES {SUBSCRIBE_MAX_RANGES}"
            )
        if req.total_bytes < 0:
            reasons.append(
                f"{label}: total_bytes must be >= 0, got {req.total_bytes}"
            )
        elif req.total_bytes > SUBSCRIBE_STREAM_MAX_BYTES:
            reasons.append(
                f"{label}: total_bytes {req.total_bytes} exceeds "
                f"SUBSCRIBE_STREAM_MAX_BYTES {SUBSCRIBE_STREAM_MAX_BYTES}"
            )
        if req.transport not in _TRANSPORT_NAMES:
            reasons.append(
                f"{label}: transport must be {TRANSPORT_UART5} (UART5) or "
                f"{TRANSPORT_USART3} (USART3), got {req.transport}"
            )
        if req.desired_hz is None or req.desired_hz <= 0:
            reasons.append(
                f"{label}: desired_hz must be > 0, got {req.desired_hz}"
            )
        if req.divider is not None:
            if not isinstance(req.divider, int) or isinstance(req.divider, bool):
                reasons.append(
                    f"{label}: divider must be an integer, got {req.divider!r}"
                )
            elif req.divider == 0:
                reasons.append(
                    f"{label}: divider=0 is the documented STOP command "
                    f"(manifest_layer.py:263), not a rate"
                )
            elif req.divider < 0:
                reasons.append(
                    f"{label}: divider must be >= 1, got {req.divider}"
                )
    if cadence_hz <= 0:
        reasons.append(f"cadence_hz must be > 0, got {cadence_hz}")

    if reasons:
        raise PlanRejected(reasons)

    slots: list[SlotPlan] = []
    for req in requests:
        divider = (req.divider if req.divider is not None
                   else choose_divider(req.desired_hz, cadence_hz))
        frame_bytes = frame_size_data_frame(req.n_ranges, req.total_bytes)
        # effective_rate_hz() is defined at the contract's 100 Hz; scale it to
        # the requested cadence. Algebraically this is cadence_hz / divider.
        achieved = (effective_rate_hz(divider)
                    * (cadence_hz / SUBSCRIBE_SEND_TASK_HZ))
        pct = _wire_pct(req.transport, frame_bytes, divider, cadence_hz)
        slots.append(SlotPlan(
            slot=req.slot,
            transport=req.transport,
            n_ranges=req.n_ranges,
            total_bytes=req.total_bytes,
            frame_bytes=frame_bytes,
            divider=divider,
            desired_hz=float(req.desired_hz),
            achieved_hz=achieved,
            wire_pct=pct,
        ))

    # Budgets are summed per transport: a USART3 byte and a UART5 byte never
    # compete for the same wire.
    transport_pct: dict[int, float] = {}
    for sp in slots:
        transport_pct[sp.transport] = transport_pct.get(sp.transport, 0.0) + sp.wire_pct

    budget_reasons: list[str] = []
    binding_transport: Optional[int] = None
    binding_needed = 0.0
    binding_budget = 0.0
    for transport, needed in sorted(transport_pct.items()):
        budget = _BUDGET_PCT[transport]
        if needed > budget:
            name = _TRANSPORT_NAMES[transport]
            contributors = ", ".join(
                f"slot {sp.slot} {sp.wire_pct:.1f}%"
                for sp in slots if sp.transport == transport
            )
            budget_reasons.append(
                f"{name} wire budget exceeded: {needed:.1f}% needed "
                f"({contributors}) > {budget}% budget "
                f"(SUBSCRIBE_BUDGET_PCT_{name}) at cadence {cadence_hz:g} Hz"
            )
            if needed / budget > binding_needed / max(binding_budget, 1e-9):
                binding_transport = transport
                binding_needed = needed
                binding_budget = budget
    if budget_reasons:
        raise PlanRejected(
            budget_reasons,
            transport=binding_transport,
            needed_pct=binding_needed,
            budget_pct=binding_budget,
        )

    return RatePlan(
        slots=tuple(sorted(slots, key=lambda sp: sp.slot)),
        cadence_hz=cadence_hz,
        transport_pct=transport_pct,
        transport_budget_pct=dict(_BUDGET_PCT),
    )


def format_plan(plan: RatePlan) -> str:
    """Render an accepted plan as a readable table.

    Follows the manifest_layer.py:547 house style:
    ``slot N: divider=D (H Hz)``.
    """
    lines = [
        f"# Rate plan: cadence {plan.cadence_hz:g} Hz Send_Task "
        f"(default {SUBSCRIBE_SEND_TASK_HZ:g} Hz from firmware_contract)",
        f"# frame size = payload + {SUBSCRIBE_STREAM_FRAME_OVERHEAD} B "
        f"overhead (frame_size_data_frame; n_ranges not counted separately)",
    ]
    for sp in plan.slots:
        name = _TRANSPORT_NAMES[sp.transport]
        lines.append(
            f"  slot {sp.slot}: divider={sp.divider} "
            f"({sp.achieved_hz:.2f} Hz, desired {sp.desired_hz:.2f} Hz, "
            f"gap {sp.rate_gap_pct:+.1f}%), transport={name}, "
            f"ranges={sp.n_ranges}, payload={sp.total_bytes} B, "
            f"frame={sp.frame_bytes} B, wire={sp.wire_pct:.2f}%"
        )
    for transport in sorted(plan.transport_pct):
        name = _TRANSPORT_NAMES[transport]
        lines.append(
            f"transport {name}: {plan.transport_pct[transport]:.2f}% of wire "
            f"/ budget {plan.transport_budget_pct[transport]}%"
        )
    return "\n".join(lines)
