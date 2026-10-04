"""WP-42 P2: wfb_safety trips and acts on SIL faults (sim/sil/faults.py). About 20 s for all five runs."""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from sim.sil import faults

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
TICK = faults.DT_C


@pytest.fixture(scope="module")
def runs() -> dict[str, faults.FaultRun]:
    return {name: faults.run(f) for name, f in faults.FAULTS.items()}


def test_link_loss_trips_heartbeat_and_lands(runs):
    r = runs["link_loss"]
    assert r.trip_name == "HEARTBEAT"
    # the last heartbeat is 0..500 ms before the fault; hb_timeout_s 1.0
    assert 0.5 - TICK <= r.events["trip"] <= 1.0 + TICK
    assert r.events["land_req"] >= r.events["trip"] and "motor_stop" not in r.events


def test_low_v_trips_after_hold_and_lands(runs):
    r = runs["low_v"]
    assert r.trip_name == "LOW_V"
    assert abs(r.events["trip"] - 3.0) <= 2 * TICK          # low_v_hold_s 3.0
    assert r.events["land_req"] >= r.events["trip"] and "motor_stop" not in r.events


def test_tilt_kills(runs):
    """KILL comes tilt_hold_s after the estimate passes tilt_deg 60, i.e. after the SIL divergence tilt (the same
    60 deg on the true attitude): the trip stops the motors of a vehicle that is already lost."""
    r = runs["tilt"]
    assert r.trip_name == "TILT"
    assert r.events["motor_stop"] == r.events["trip"] <= 1.0
    assert "land_req" not in r.events
    assert r.events["crash"] < r.events["motor_stop"]


def test_fence_push_back_without_trip(runs):
    r = runs["fence_push"]
    assert r.trip_name == "NONE" and r.events["push"] <= TICK
    assert "land_req" not in r.events and "motor_stop" not in r.events
    after = r.t >= 0
    assert np.nanmax(np.abs(r.x_est[after])) < 1.6 + 0.3   # never fence_over_m beyond
    assert abs(r.x_est[-1]) <= 1.6 and r.push[-1] == 0      # pushed back inside, push cleared


def test_fence_over_lands_in_place(runs):
    r = runs["fence_over"]
    assert r.trip_name == "FENCE"
    assert r.events["trip"] <= TICK and r.events["land_req"] == r.events["trip"]
    assert "motor_stop" not in r.events
