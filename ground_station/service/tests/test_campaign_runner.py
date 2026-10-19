"""Unit tests for CampaignRunner ground trajectory preload (Task 4 / Fix 2)."""

from __future__ import annotations

from unittest.mock import Mock
from ground_station.platform.wfb_commands import CMD_TRAJ, TrajIdx, WfbClient
from ground_station.service.campaign_runner import (
    CampaignRunner,
    RunnerDeps,
    fly_scenario,
)
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.scenario_schema import parse_scenario
from ground_station.service.tests.test_campaign_live import FakeClock, create_deps


def _make_scenario() -> dict:
    return {
        "scenario": "preload_test",
        "steps": [
            {"takeoff": {"z": 0.8}},
            {"goto": {"x": 0.4, "y": 0.2, "z": 0.8, "dwell_s": 1.0}},
            "land",
        ],
    }


def test_campaign_runner_pre_arm_preload_skips_takeoff_upload():
    """upload_preload() runs on the ground before RC arm; takeoff skips upload."""
    drone = FakeDrone()
    drone.sbus_live = True
    clock = FakeClock()
    client = WfbClient(drone.send)
    deps = create_deps(drone, client, clock)

    scenario = parse_scenario(_make_scenario())
    runner = CampaignRunner(scenario, deps)
    assert runner._preload_uploaded is False

    # 1. Preload on the ground before RC arm
    ok = runner.upload_preload()
    assert ok is True
    assert runner._preload_uploaded is True

    # Trajectory was committed on drone before flight
    traj_status = drone.status()["traj_state"]
    assert traj_status == 2

    # Wrap client.traj_begin to track calls during flight
    client.traj_begin = Mock(wraps=client.traj_begin)

    # 2. Run the flight
    outcome = runner.run()
    assert outcome.landed is True
    assert not outcome.aborted

    # Takeoff did NOT call preload_send again (0 new traj_begin calls)
    assert client.traj_begin.call_count == 0


def test_campaign_runner_legacy_takeoff_upload():
    """Without pre-arm upload_preload(), takeoff uploads trajectory as before."""
    drone = FakeDrone()
    drone.sbus_live = True
    clock = FakeClock()
    client = WfbClient(drone.send)
    client.traj_begin = Mock(wraps=client.traj_begin)
    deps = create_deps(drone, client, clock)

    scenario = parse_scenario(_make_scenario())
    runner = CampaignRunner(scenario, deps)
    assert runner._preload_uploaded is False

    # Run without calling upload_preload() first
    outcome = runner.run()
    assert outcome.landed is True
    assert not outcome.aborted

    # Takeoff uploaded the trajectory during ground setup before lift-off
    assert client.traj_begin.call_count == 1
    assert runner._preload_uploaded is True


def test_campaign_runner_preload_noop_when_no_trajectory():
    """upload_preload() returns True when there is no trajectory to preload."""
    drone = FakeDrone()
    drone.sbus_live = True
    clock = FakeClock()
    client = WfbClient(drone.send)
    deps = create_deps(drone, client, clock)

    scenario = parse_scenario({
        "scenario": "hover_only",
        "steps": [
            {"takeoff": {"z": 0.8}},
            {"hold": {"s": 2.0}},
            "land",
        ],
    })
    runner = CampaignRunner(scenario, deps)
    assert runner.upload_preload() is True
    outcome = runner.run()
    assert outcome.landed is True
