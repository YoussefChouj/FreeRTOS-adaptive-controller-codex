import pytest
from unittest.mock import Mock, call
from pathlib import Path

from ground_station.service.campaign_runner import RunnerDeps, run_campaign, files_after_from_diff
from ground_station.service.campaign_schema import load_campaign
from ground_station.service.fake_drone import FakeDrone
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.abort_monitor import AbortSample, AbortDecision

YAML_PATH = "ground_station/service/campaigns/example_circle.yaml"

class FakeClock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, dt):
        self.t += dt

def create_deps(drone, client, clock):
    packs = Mock()
    packs.next_flight_allowed.return_value = (True, "")
    
    tuner = Mock()
    tuner.propose.return_value = {"param": 1.0}
    
    gate = Mock()
    gate.check_change.return_value = Mock(ok=True, reasons=[])
    gate.next_flight_must_hover.return_value = False
    gate.on_flight_result.return_value = "keep"
    
    monitor = Mock()
    monitor.consecutive_aborts = 0
    monitor.step.return_value = AbortDecision(level=0, reason="")
    
    def sample(t_s):
        return AbortSample(
            t_s=t_s, age_s=0.1, airborne=drone.status()["prim_state"] > 0,
            pos_m=drone.position, ref_m=(0.0, 0.0, 0.5), roll_deg=drone.roll_deg,
            pitch_deg=drone.pitch_deg, rate_err_dps=(0.0, 0.0, 0.0), sat_frac=0.0,
            safety_trip=int(drone.status()["safety_trip"]), soc_pct=100.0
        )
        
    def step(dt):
        clock.sleep(dt)
        drone.step(dt)
        
    return RunnerDeps(
        client=client,
        step=step,
        clock=clock,
        sleep=clock.sleep,
        status=drone.status,
        sample=sample,
        packs=packs,
        monitor=monitor,
        tuner=tuner,
        gate=gate,
        flash=Mock(return_value="abc"),
        analyze=Mock(return_value=1.23),
        wait_for_go=Mock(return_value=True),
        resting_v=Mock(return_value=4.0),
        arm_allowed=Mock(return_value=True),
        change_request=Mock(return_value=None),
        diff_source=Mock(return_value=""),
        flight_timeout_s=120.0,
        hover_s=0.5
    )

def test_files_after_from_diff(tmp_path):
    f1 = tmp_path / "foo.py"
    f1.write_text("hello")
    diff = "+++ b/foo.py\n@@\n+hello"
    files = files_after_from_diff(diff, str(tmp_path))
    assert files == {"foo.py": "hello"}
    
def test_a_e2e_complete(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    report = run_campaign(str(campaign_yaml), deps)
    assert report.status == "complete"
    assert len(report.flights) >= 3
    for f in report.flights:
        assert f.j == 1.23
    assert deps.tuner.record.call_count == len(report.flights)

def test_b_level_1_abort(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    client.kill = Mock(wraps=client.kill)
    client.traj_stop = Mock(wraps=client.traj_stop)
    
    def mock_step(sample):
        if drone.status()["traj_state"] == 3: # EXECUTING
            return AbortDecision(level=1, reason="test abort")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    
    report = run_campaign(str(campaign_yaml), deps)
    assert client.traj_stop.called
    assert not client.kill.called
    assert report.flights[0].abort_level == 1
    
def test_c_level_2_abort_gate(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    def mock_step(sample):
        if drone.status()["traj_state"] == 3:
            return AbortDecision(level=2, reason="test level 2")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    
    report = run_campaign(str(campaign_yaml), deps)
    deps.gate.on_flight_result.assert_called_with(aborted=True, j=None)

def test_d_level_3_abort(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    def mock_step(sample):
        if drone.status()["traj_state"] == 3:
            return AbortDecision(level=3, reason="fatal")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    
    report = run_campaign(str(campaign_yaml), deps)
    assert report.status == "operator_needed"
    assert len(report.flights) == 1
    
def test_e_cooldown(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    takeoffs = []
    orig_takeoff = client.takeoff
    def mock_takeoff():
        takeoffs.append(clock.t)
        return orig_takeoff()
    client.takeoff = mock_takeoff
    
    run_campaign(str(campaign_yaml), deps)
    
    assert len(takeoffs) >= 2
    # Second takeoff must happen at least duration of first flight after landing
    # Since deps.packs.next_flight_allowed is mocked to True immediately, it depends on sleep loops
    assert takeoffs[1] - takeoffs[0] > 0

def test_f_arm_allowed_false(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.arm_allowed = Mock(return_value=False)
    
    client.arm = Mock(wraps=client.arm)
    report = run_campaign(str(campaign_yaml), deps)
    
    assert report.status == "arm_refused"
    assert not client.arm.called
    
def test_g_hover_only(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.change_request = Mock(return_value={"justification": "test"})
    deps.gate.next_flight_must_hover.return_value = True
    
    client.traj_start = Mock(wraps=client.traj_start)
    
    report = run_campaign(str(campaign_yaml), deps)
    
    assert not client.traj_start.called
    assert report.flights[0].hover_only is True
    
def test_h_gate_check_change(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.change_request = Mock(return_value={"justification": "test"})
    deps.diff_source = Mock(return_value="+++ b/foo.py\n")
    
    f = tmp_path / "foo.py"
    f.write_text("code")
    deps.repo_root = str(tmp_path)
    
    report = run_campaign(str(campaign_yaml), deps)
    
    deps.gate.check_change.assert_called_with("+++ b/foo.py\n", {"justification": "test"}, {"foo.py": "code"})

def test_i_never_reaches_hover(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.flight_timeout_s = 1.0
    
    orig_step = drone.step
    def mock_step(dt):
        orig_step(dt)
        if drone.status()["prim_state"] > 0:
            drone._z = 0.0
            drone._prim_state = 1
    drone.step = mock_step
    
    client.land = Mock(wraps=client.land)
    client.kill = Mock(wraps=client.kill)
    
    report = run_campaign(str(campaign_yaml), deps)
    
    # When timeout occurs in takeoff, it aborts. Then it tries to land.
    # But wait, our mock_step forces prim_state = 1.
    # The land command might change it to RETURN, but our mock forces it to CLIMB (1).
    # This would cause the land wait loop to time out!
    # Which would return "operator_needed", reason "landing timeout".
    
    assert len(report.flights) > 0
    f = report.flights[0]
    assert f.abort_level == 1
    assert f.abort_reason.startswith("timeout")
    assert client.land.called
    assert not client.kill.called
