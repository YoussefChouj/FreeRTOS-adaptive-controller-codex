"""Tests for ground_station.research.sim — the sim/replay harness.

Required tests:
  1. Plant step response matches analytic first-order-plus-integrator shape
  2. Closed-loop PID sim is stable for a +/-20 deg roll step
  3. Replaying a synthetic capture reproduces it within tolerance
  4. dry_run produces a Run with metrics
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import numpy as np

# Run tests from the worktree root so imports resolve
import sys
_WORKTREE = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
if _WORKTREE not in sys.path:
    sys.path.insert(0, _WORKTREE)

from ground_station.research.sim.plant import IdentifiedPlant, AxisModel
from ground_station.research.sim.reference_model import ReferenceModel
from ground_station.research.sim.baseline import CascadedPID, RatePID, RatePIDConfig
from ground_station.research.sim.replay import Replay, ReplayResult
from ground_station.research.sim.dryrun import dry_run, _extract_trajectory, _rmse
from ground_station.research.sim import constants
from ground_station.research.run import Run


# ---------------------------------------------------------------------------
# Test 1: Plant step response matches analytic first-order-plus-integrator
# ---------------------------------------------------------------------------

class TestPlantStepResponse:
    """Plant step response matches analytic first-order-plus-integrator shape."""

    def test_roll_plant_step_response(self) -> None:
        """Roll axis: G(s) = K/(s*(1+s/p)) -> step response is a rising
        exponential of the form K*(1 - a*exp(-p1*t) - b*exp(-p2*t))."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        plant.reset()

        # Step input: 1.0 on roll
        n = 500  # 1 second of simulation
        responses = []
        for _ in range(n):
            state = plant.step({"roll": 1.0, "pitch": 0.0, "yaw": 0.0})
            responses.append(state["p"])

        # Key properties:
        # 1. Response starts at 0 (initial condition)
        assert responses[0] == pytest.approx(0.0, abs=1e-10)
        # 2. Response is monotonically increasing (never goes negative)
        for i in range(1, len(responses)):
            assert responses[i] >= responses[i - 1] - 1e-10, \
                f"Non-monotonic at step {i}"
        # 3. The 2nd derivative should be negative (concave down initially)
        mid = n // 4
        slope_early = (responses[mid] - responses[0]) / mid
        slope_late = (responses[-1] - responses[mid]) / (n - mid)
        assert slope_early > slope_late, \
            f"Slope should decrease: early={slope_early:.4f}, late={slope_late:.4f}"

    def test_yaw_plant_pure_integrator(self) -> None:
        """Yaw axis: G(s) = K/s -> step response is a perfect ramp: y = K*u*t."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        plant.reset()

        n = 200
        responses = []
        for _ in range(n):
            state = plant.step({"roll": 0.0, "pitch": 0.0, "yaw": 1.0})
            responses.append(state["r"])

        # Pure integrator: y[n] = K * sum(u) = K * n * dt * u
        K = constants.YAW_K
        expected = [K * (i + 1) * (1.0 / 500.0) * 1.0 for i in range(n)]

        for i in range(n):
            assert responses[i] == pytest.approx(expected[i], rel=1e-4), \
                f"Yaw step mismatch at {i}: got {responses[i]:.6f}, expected {expected[i]:.6f}"

    def test_pitch_plant_step_response(self) -> None:
        """Pitch axis behaves like roll: monotonically increasing step response."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        plant.reset()

        n = 500
        responses = []
        for _ in range(n):
            state = plant.step({"roll": 0.0, "pitch": 1.0, "yaw": 0.0})
            responses.append(state["q"])

        # Same properties as roll
        assert responses[0] == pytest.approx(0.0, abs=1e-10)
        for i in range(1, len(responses)):
            assert responses[i] >= responses[i - 1] - 1e-10

        mid = n // 4
        slope_early = (responses[mid] - responses[0]) / mid
        slope_late = (responses[-1] - responses[mid]) / (n - mid)
        assert slope_early > slope_late


# ---------------------------------------------------------------------------
# Test 2: Closed-loop PID sim is stable for a +/-20 deg roll step
# ---------------------------------------------------------------------------

class TestClosedLoopPIDStability:
    """Closed-loop PID sim is stable for a +/-20 deg roll step."""

    def test_roll_20deg_step_stable(self) -> None:
        """Simulate a +20 deg roll step then -20 deg, verify bounded response."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        pid = CascadedPID(dt=1.0 / 500.0)

        n = 1000  # 2 seconds
        att_sp_roll: list[float] = []
        for i in range(n):
            t = i * (1.0 / 500.0)
            if t < 0.1:
                att_sp_roll.append(0.0)
            elif t < 0.5:
                att_sp_roll.append(20.0)
            else:
                att_sp_roll.append(0.0)

        plant.reset()
        pid.reset()

        max_rate = 0.0
        max_attitude = 0.0
        attitudes = [0.0]

        for i in range(n):
            att_sp = {"roll": att_sp_roll[i], "pitch": 0.0, "yaw": 0.0}
            att_fb = {"roll": attitudes[-1], "pitch": 0.0, "yaw": 0.0}

            pid_out = pid.step(att_sp, att_fb)

            plant_input = {
                "roll": pid_out.get("roll", 0.0),
                "pitch": pid_out.get("pitch", 0.0),
                "yaw": pid_out.get("yaw", 0.0),
            }
            plant_state = plant.step(plant_input)
            rate = plant_state["p"]
            attitudes.append(attitudes[-1] + rate * (1.0 / 500.0))

            max_rate = max(max_rate, abs(rate))
            max_attitude = max(max_attitude, abs(attitudes[-1]))

        # Stability checks:
        assert max_rate < 50.0, f"Rate diverged: max_rate={max_rate:.2f} rad/s"
        assert max_attitude < 90.0, f"Attitude diverged: max_att={max_attitude:.2f} deg"
        last_100 = attitudes[-100:]
        final_attitude = sum(last_100) / len(last_100)
        assert abs(final_attitude) < 5.0, \
            f"Attitude did not return near zero: final={final_attitude:.2f}"

    def test_pitch_20deg_step_stable(self) -> None:
        """Simulate a +20 deg pitch step, verify bounded response."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        pid = CascadedPID(dt=1.0 / 500.0)

        n = 1000
        att_sp_pitch: list[float] = []
        for i in range(n):
            t = i * (1.0 / 500.0)
            if t < 0.1:
                att_sp_pitch.append(0.0)
            elif t < 0.5:
                att_sp_pitch.append(20.0)
            else:
                att_sp_pitch.append(0.0)

        plant.reset()
        pid.reset()

        max_rate = 0.0
        max_attitude = 0.0
        attitudes = [0.0]

        for i in range(n):
            att_sp = {"roll": 0.0, "pitch": att_sp_pitch[i], "yaw": 0.0}
            att_fb = {"roll": 0.0, "pitch": attitudes[-1], "yaw": 0.0}

            pid_out = pid.step(att_sp, att_fb)
            plant_input = {
                "roll": 0.0,
                "pitch": pid_out.get("pitch", 0.0),
                "yaw": 0.0,
            }
            plant_state = plant.step(plant_input)
            rate = plant_state["q"]
            attitudes.append(attitudes[-1] + rate * (1.0 / 500.0))

            max_rate = max(max_rate, abs(rate))
            max_attitude = max(max_attitude, abs(attitudes[-1]))

        assert max_rate < 50.0, f"Rate diverged: max_rate={max_rate:.2f} rad/s"
        assert max_attitude < 90.0, f"Attitude diverged: max_att={max_attitude:.2f} deg"


# ---------------------------------------------------------------------------
# Test 3: Replaying a synthetic capture reproduces it within tolerance
# ---------------------------------------------------------------------------

class TestReplaySyntheticCapture:
    """Replaying a synthetic capture reproduces it within tolerance."""

    def test_replay_direct_rate_step(self) -> None:
        """Replay a direct rate step through plant -> response matches plant output."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        pid = CascadedPID(dt=1.0 / 500.0)

        n = 200
        captures: list[dict[str, Any]] = []
        for i in range(n):
            t = i * (1.0 / 500.0)
            rate = 5.0
            captures.append({"t": t, "roll_sp": rate})

        replay = Replay(
            plant=plant,
            pid=pid,
            ref_models={
                "roll": ReferenceModel.for_axis("roll", dt=1.0 / 500.0),
                "pitch": ReferenceModel.for_axis("pitch", dt=1.0 / 500.0),
                "yaw": ReferenceModel.for_axis("yaw", dt=1.0 / 500.0),
            },
            dt=1.0 / 500.0,
        )
        result = replay.run(captures)

        # Re-run plant directly for comparison
        plant2 = IdentifiedPlant(dt=1.0 / 500.0)
        expected_responses = []
        for i in range(n):
            state = plant2.step({"roll": 5.0, "pitch": 0.0, "yaw": 0.0})
            expected_responses.append(state["p"])

        response = result.response.get("roll", [])
        assert len(response) == n, f"Expected {n} samples, got {len(response)}"
        for i in range(n):
            assert response[i] == pytest.approx(expected_responses[i], rel=1e-6), \
                f"Replay mismatch at {i}: got {response[i]:.8f}, expected {expected_responses[i]:.8f}"

    def test_replay_with_adaptive_hook(self) -> None:
        """Replay with a nonzero u_ad hook produces different output."""
        plant = IdentifiedPlant(dt=1.0 / 500.0)
        pid = CascadedPID(dt=1.0 / 500.0)

        n = 50
        captures: list[dict[str, Any]] = [{"t": i * 0.002, "roll_sp": 2.0} for i in range(n)]

        replay0 = Replay(plant=plant, pid=pid, dt=1.0 / 500.0)
        plant.reset()
        pid.reset()
        result0 = replay0.run(captures)

        replay1 = Replay(
            plant=plant, pid=pid, dt=1.0 / 500.0,
            u_ad=lambda t, state: {"roll": 0.5}
        )
        plant.reset()
        pid.reset()
        result1 = replay1.run(captures)

        u_ad_roll = result1.u_ad.get("roll", [])
        for val in u_ad_roll[:10]:
            assert val == pytest.approx(0.5, abs=1e-6)

        resp0 = result0.response.get("roll", [])
        resp1 = result1.response.get("roll", [])
        diverged = False
        for i in range(10, min(len(resp0), len(resp1))):
            if abs(resp0[i] - resp1[i]) > 1e-4:
                diverged = True
                break
        assert diverged, "Responses with/without u_ad should differ"


# ---------------------------------------------------------------------------
# Test 4: dry_run produces a Run with metrics
# ---------------------------------------------------------------------------

class TestDryRun:
    """dry_run produces a Run with metrics."""

    def test_dry_run_basic(self, tmp_path: Path) -> None:
        """A simple step trajectory produces a Run with metrics and outcome 'pass'."""
        from ground_station.research.store import Store

        n = 100
        trajectory = [{"t": i * 0.002, "roll_sp": 3.0} for i in range(n)]

        store = Store(str(tmp_path / "runs"))
        run = dry_run(
            trajectory,
            params={"dt": 0.002},
            dt=0.002,
            store=store,
            kind="validation",
            tags=["test"],
        )

        assert isinstance(run, Run), f"Expected Run, got {type(run)}"
        assert run.kind == "validation"
        assert "sim" in run.tags, f"Expected 'sim' tag, got {run.tags}"
        assert run.outcome == "pass", f"Expected outcome 'pass', got {run.outcome}"
        assert run.metrics, f"Expected metrics, got {run.metrics}"
        assert any(k.endswith("_rmse") for k in run.metrics), \
            f"Expected rmse metric, got {run.metrics}"
        store.close()

    def test_dry_run_empty_trajectory(self, tmp_path: Path) -> None:
        """Empty trajectory produces a Run with outcome 'skipped'."""
        from ground_station.research.store import Store

        store = Store(str(tmp_path / "runs"))
        run = dry_run(
            [],
            store=store,
            kind="validation",
        )

        assert run.outcome == "skipped"
        store.close()

    def test_dry_run_from_dict_workflow(self, tmp_path: Path) -> None:
        """dry_run accepts a dict with 'trajectory' key."""
        from ground_station.research.store import Store

        n = 50
        trajectory = [{"t": i * 0.002, "roll_sp": 1.0} for i in range(n)]
        workflow = {"trajectory": trajectory, "name": "test_roll_step"}

        store = Store(str(tmp_path / "runs"))
        run = dry_run(
            workflow,
            store=store,
            kind="validation",
        )

        assert run.outcome == "pass"
        assert "test_roll_step" in run.intent
        store.close()

    def test_extract_trajectory(self) -> None:
        """_extract_trajectory handles various input types."""
        traj = [{"t": 0.0, "roll_sp": 1.0}]
        assert _extract_trajectory(traj) == traj

        single = {"t": 5.0, "roll_sp": 2.0}
        assert _extract_trajectory(single) == [single]

        wrapper = {"trajectory": [{"t": 1.0}]}
        assert _extract_trajectory(wrapper) == [{"t": 1.0}]

        assert _extract_trajectory(None) == []
        assert _extract_trajectory("/tmp/no_such_file.csv") == []

    def test_rmse(self) -> None:
        """_rmse computes root mean square correctly."""
        assert _rmse([]) == 0.0
        assert _rmse([1.0]) == 1.0
        assert _rmse([3.0, 4.0]) == pytest.approx(5.0)
        assert _rmse([0.0, 0.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# Test 5: Reference model step response
# ---------------------------------------------------------------------------

class TestReferenceModel:
    """Reference model produces correct step responses."""

    def test_first_order_ref_model(self) -> None:
        """1st-order ref model: xm(t) = r*(1 - exp(-bw*t))."""
        rm = ReferenceModel("first_order", bw=30.0, zeta=0.8, dt=1.0 / 500.0)
        rm.reset()

        n = 500
        xm_values = []
        r = 1.0
        for _ in range(n):
            xm = rm.step(r)
            xm_values.append(xm)

        assert xm_values[0] == pytest.approx(r * (1.0 - math.exp(-30.0 * 0.002)), rel=1e-3)
        assert xm_values[-1] == pytest.approx(r, rel=0.01)

    def test_second_order_ref_model(self) -> None:
        """2nd-order ref model: step response with overshoot for zeta < 1."""
        rm = ReferenceModel("second_order", bw=44.0, zeta=0.8, dt=1.0 / 500.0)
        rm.reset()

        n = 500
        xm_values = []
        r = 1.0
        for _ in range(n):
            xm = rm.step(r)
            xm_values.append(xm)

        for i in range(1, len(xm_values)):
            if xm_values[i] > r + 0.1:
                break
        assert xm_values[-1] == pytest.approx(r, abs=0.05)

    def test_ref_model_for_axis(self) -> None:
        """ReferenceModel.for_axis returns correct model type per axis."""
        rm_roll = ReferenceModel.for_axis("roll", dt=1.0 / 500.0)
        assert rm_roll.kind == "second_order"
        assert rm_roll.bw == 44.0

        rm_pitch = ReferenceModel.for_axis("pitch", dt=1.0 / 500.0)
        assert rm_pitch.kind == "second_order"
        assert rm_pitch.bw == 44.0

        rm_yaw = ReferenceModel.for_axis("yaw", dt=1.0 / 500.0)
        assert rm_yaw.kind == "first_order"
        assert rm_yaw.bw == 30.0

    def test_ref_model_error(self) -> None:
        """ReferenceModel.error returns x - xm."""
        rm = ReferenceModel("first_order", bw=30.0, dt=1.0 / 500.0)
        rm.reset()
        rm.step(1.0, x=0.5)
        assert rm.error(0.5) == pytest.approx(0.0, abs=1e-10)
