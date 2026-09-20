"""Tests for the WP5 multi-slot rate planner.

The real-plan regression uses slot sizes measured through the actual
PresetManager/SymbolResolver packing against OBJ/JX_FLY.axf (2026-09-20):

  * slot 1  subscribe_presets.py:26 example, imu-pid vars defined at
            subscribe_presets.py:186-213 (divider=4, transport=1 USART3):
            6 packed ranges, 56 payload B.
  * slot 2  subscribe_presets.py:32 example, merge(boot-default, imu-pid),
            divider=20, USART3: 7 packed ranges, 58 payload B.
  * slot 0  manifest_layer.py:293-307 (divider=20, TRANSPORT_USART3, slot 0)
            over boot_default_layout.BOOT_DEFAULT_VARS
            (boot_default_layout.py:56-77): 8 packed ranges, 36 payload B.
"""

from __future__ import annotations

import pytest

from ground_station.platform import rate_planner as rp
from ground_station.platform.rate_planner import (
    PlanRejected,
    RatePlan,
    SlotPlan,
    SlotRequest,
    format_plan,
    plan_slots,
)
from ground_station.platform.firmware_contract import (
    SUBSCRIBE_MAX_SLOTS,
    SUBSCRIBE_MAX_RANGES,
    SUBSCRIBE_STREAM_MAX_BYTES,
    SUBSCRIBE_BUDGET_PCT_USART3,
    SUBSCRIBE_BUDGET_PCT_UART5,
    SUBSCRIBE_SEND_TASK_HZ,
    frame_size_data_frame,
)

U5 = rp.TRANSPORT_UART5
U3 = rp.TRANSPORT_USART3


def _find(plan: RatePlan, slot: int) -> SlotPlan:
    matches = [sp for sp in plan.slots if sp.slot == slot]
    assert len(matches) == 1
    return matches[0]


# --- a plan that fits -------------------------------------------------------

def test_simple_plan_fits():
    # imu-pid sized slot: 14 logical vars pack to fewer ranges; 56 B payload.
    req = SlotRequest(slot=1, n_ranges=6, total_bytes=56,
                      transport=U3, desired_hz=25.0)
    plan = plan_slots([req])
    sp = _find(plan, 1)
    assert sp.divider == 4                       # 100 / 25
    assert sp.achieved_hz == pytest.approx(25.0)
    assert sp.frame_bytes == frame_size_data_frame(6, 56) == 68
    # 68 B * 25 Hz = 1700 B/s on a 92160 B/s wire
    assert sp.wire_pct == pytest.approx(1700 / 92160 * 100)
    assert plan.transport_pct[U3] == pytest.approx(sp.wire_pct)
    assert plan.transport_budget_pct[U3] == SUBSCRIBE_BUDGET_PCT_USART3


# --- UART5 20% budget rejection ---------------------------------------------

def test_uart5_budget_rejected_names_binding_constraint():
    # 800 B frames at divider 34 = 2353 B/s = 20.4% of the 11520 B/s wire.
    req = SlotRequest(slot=0, n_ranges=40, total_bytes=788,
                      transport=U5, desired_hz=100 / 34, divider=34)
    with pytest.raises(PlanRejected) as exc:
        plan_slots([req])
    rej = exc.value
    assert rej.transport == U5
    assert rej.needed_pct == pytest.approx(20.425, abs=0.01)
    assert rej.budget_pct == SUBSCRIBE_BUDGET_PCT_UART5 == 20
    msg = str(rej)
    assert "UART5" in msg
    assert "20.4%" in msg
    assert "20% budget" in msg
    assert "SUBSCRIBE_BUDGET_PCT_UART5" in msg


def test_uart5_just_under_budget_accepted():
    # Same frame at divider 35 = 19.8%, just inside the 20% budget.
    req = SlotRequest(slot=0, n_ranges=40, total_bytes=788,
                      transport=U5, desired_hz=100 / 35, divider=35)
    plan = plan_slots([req])
    assert plan.transport_pct[U5] == pytest.approx(19.84, abs=0.01)


# --- structural limits ------------------------------------------------------

def test_too_many_ranges_rejected():
    req = SlotRequest(slot=0, n_ranges=SUBSCRIBE_MAX_RANGES + 1,
                      total_bytes=10, transport=U3, desired_hz=10.0)
    with pytest.raises(PlanRejected) as exc:
        plan_slots([req])
    assert any(str(SUBSCRIBE_MAX_RANGES) in r for r in exc.value.reasons)


def test_too_many_payload_bytes_rejected():
    req = SlotRequest(slot=0, n_ranges=1,
                      total_bytes=SUBSCRIBE_STREAM_MAX_BYTES + 1,
                      transport=U3, desired_hz=10.0)
    with pytest.raises(PlanRejected) as exc:
        plan_slots([req])
    assert any(str(SUBSCRIBE_STREAM_MAX_BYTES) in r for r in exc.value.reasons)


def test_more_than_four_slots_rejected():
    reqs = [
        SlotRequest(slot=i, n_ranges=1, total_bytes=4,
                    transport=U3, desired_hz=10.0)
        for i in range(SUBSCRIBE_MAX_SLOTS + 1)
    ]
    with pytest.raises(PlanRejected) as exc:
        plan_slots(reqs)
    assert any(f"at most {SUBSCRIBE_MAX_SLOTS}" in r for r in exc.value.reasons)


def test_four_slots_is_the_limit_and_fits():
    reqs = [
        SlotRequest(slot=i, n_ranges=1, total_bytes=4,
                    transport=U3, desired_hz=10.0)
        for i in range(SUBSCRIBE_MAX_SLOTS)
    ]
    plan = plan_slots(reqs)
    assert len(plan.slots) == SUBSCRIBE_MAX_SLOTS


def test_divider_zero_is_stop_not_rate():
    req = SlotRequest(slot=0, n_ranges=1, total_bytes=4,
                      transport=U3, desired_hz=10.0, divider=0)
    with pytest.raises(PlanRejected) as exc:
        plan_slots([req])
    assert any("STOP" in r for r in exc.value.reasons)


def test_bad_transport_and_duplicate_slot_rejected():
    reqs = [
        SlotRequest(slot=0, n_ranges=1, total_bytes=4,
                    transport=7, desired_hz=10.0),
        SlotRequest(slot=0, n_ranges=1, total_bytes=4,
                    transport=U3, desired_hz=10.0),
    ]
    with pytest.raises(PlanRejected) as exc:
        plan_slots(reqs)
    joined = " | ".join(exc.value.reasons)
    assert "transport" in joined
    assert "more than once" in joined


# --- desired vs achieved honesty --------------------------------------------

def test_desired_30_hz_at_100_cadence_is_25_not_30():
    req = SlotRequest(slot=0, n_ranges=1, total_bytes=4,
                      transport=U3, desired_hz=30.0)
    plan = plan_slots([req])
    sp = _find(plan, 0)
    assert sp.divider == 4                 # conservative ladder, never divider 3
    assert sp.achieved_hz == pytest.approx(25.0)
    assert sp.rate_gap_pct == pytest.approx((25.0 - 30.0) / 30.0 * 100)
    rendered = format_plan(plan)
    assert "25.00 Hz, desired 30.00 Hz" in rendered
    assert "-16.7%" in rendered


def test_pinned_divider_overshoot_is_reported_not_corrected():
    # Pin divider 3 -> 33.3 Hz although only 30 Hz was wanted.
    req = SlotRequest(slot=0, n_ranges=1, total_bytes=4,
                      transport=U3, desired_hz=30.0, divider=3)
    plan = plan_slots([req])
    sp = _find(plan, 0)
    assert sp.divider == 3
    assert sp.achieved_hz == pytest.approx(100.0 / 3)
    assert sp.rate_gap_pct > 0


def test_default_cadence_comes_from_contract():
    plan = plan_slots([SlotRequest(0, 1, 4, U3, 25.0)])
    assert plan.cadence_hz == SUBSCRIBE_SEND_TASK_HZ == 100


def test_cadence_parameter_scales_rate_and_wire():
    req = SlotRequest(slot=0, n_ranges=6, total_bytes=56,
                      transport=U3, desired_hz=50.0, divider=4)
    p100 = plan_slots([req], cadence_hz=100.0)
    p200 = plan_slots([req], cadence_hz=200.0)
    assert _find(p200, 0).achieved_hz == pytest.approx(50.0)
    assert _find(p100, 0).achieved_hz == pytest.approx(25.0)
    assert _find(p200, 0).wire_pct == pytest.approx(
        _find(p100, 0).wire_pct * 2.0)
    # Measured MIXED-mode cadence is fractional-ish; 80 Hz must be accepted.
    p80 = plan_slots([req], cadence_hz=80.0)
    assert _find(p80, 0).achieved_hz == pytest.approx(20.0)


# --- the two transports are budgeted separately ------------------------------

def test_transports_budgeted_separately_not_summed():
    # The same 800 B frame: 86.8% of USART3 at divider 1 would, if summed
    # across wires, combine with the 19.8% UART5 slot past 95% -- they are
    # different links and must be judged independently.
    reqs = [
        SlotRequest(slot=1, n_ranges=40, total_bytes=788,
                    transport=U3, desired_hz=100.0, divider=1),
        SlotRequest(slot=2, n_ranges=40, total_bytes=788,
                    transport=U5, desired_hz=100 / 35, divider=35),
    ]
    plan = plan_slots(reqs)
    assert plan.transport_pct[U3] == pytest.approx(86.81, abs=0.01)
    assert plan.transport_pct[U5] == pytest.approx(19.84, abs=0.01)
    assert set(plan.transport_pct) == {U3, U5}


def test_two_uart5_slots_share_one_budget():
    # 15% + 15% = 30% on the same UART5 wire: rejected even though each
    # contributor alone would fit the 20% budget.
    reqs = [
        SlotRequest(slot=0, n_ranges=10, total_bytes=200,
                    transport=U5, desired_hz=10.0, divider=10),
        SlotRequest(slot=1, n_ranges=10, total_bytes=200,
                    transport=U5, desired_hz=10.0, divider=10),
    ]
    with pytest.raises(PlanRejected) as exc:
        plan_slots(reqs)
    assert exc.value.transport == U5
    msg = str(exc.value)
    assert "slot 0" in msg and "slot 1" in msg


# --- the hand-tuned plan that is actually flying -----------------------------

# Measured packed sizes, see module docstring for file:line provenance.
FLYING_SLOTS = [
    SlotRequest(slot=0, n_ranges=8, total_bytes=36, transport=U3,
                desired_hz=10.0, divider=20),
    SlotRequest(slot=1, n_ranges=6, total_bytes=56, transport=U3,
                desired_hz=50.0, divider=4),
    SlotRequest(slot=2, n_ranges=7, total_bytes=58, transport=U3,
                desired_hz=10.0, divider=20),
]


@pytest.mark.parametrize("cadence", [100.0, 200.0, 80.0])
def test_flying_plan_fits_at_every_documented_cadence(cadence):
    plan = plan_slots(list(FLYING_SLOTS), cadence_hz=cadence)
    total = plan.transport_pct[U3]
    # Even at the 200 Hz nominal upper bound the three slots stay under 5%.
    assert total < SUBSCRIBE_BUDGET_PCT_USART3
    if cadence == 200.0:
        assert total == pytest.approx(4.97, abs=0.01)
        assert _find(plan, 0).achieved_hz == pytest.approx(10.0)
        assert _find(plan, 1).achieved_hz == pytest.approx(50.0)
        assert _find(plan, 2).achieved_hz == pytest.approx(10.0)


def test_flying_plan_per_slot_arithmetic_at_200_hz():
    plan = plan_slots(list(FLYING_SLOTS), cadence_hz=200.0)
    by_slot = {sp.slot: sp for sp in plan.slots}
    # frame = 12 + payload: 48 / 68 / 70 bytes; bps = frame * 200 / divider.
    assert by_slot[0].frame_bytes == 48
    assert by_slot[1].frame_bytes == 68
    assert by_slot[2].frame_bytes == 70
    assert by_slot[0].wire_pct == pytest.approx(480 / 92160 * 100)
    assert by_slot[1].wire_pct == pytest.approx(3400 / 92160 * 100)
    assert by_slot[2].wire_pct == pytest.approx(700 / 92160 * 100)
    # format_plan follows the manifest_layer "slot N: divider=D (H Hz)" style.
    rendered = format_plan(plan)
    assert "slot 0: divider=20 (10.00 Hz" in rendered
    assert "slot 1: divider=4 (50.00 Hz" in rendered
    assert "slot 2: divider=20 (10.00 Hz" in rendered
