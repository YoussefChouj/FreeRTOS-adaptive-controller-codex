from unittest.mock import Mock, call
from pathlib import Path
from ground_station.service.campaign_runner import RunnerDeps, run_campaign, files_after_from_diff
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
    from ground_station.service.campaign_runner import RunnerControl
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
        resting_v=Mock(return_value=16.0),
        arm_allowed=Mock(return_value=True),
        change_request=Mock(return_value=None),
        diff_source=Mock(return_value=""),
        flight_timeout_s=120.0,
        hover_s=0.5,
        control=RunnerControl(),
        apply_params=Mock(return_value=True),
        dt_s=0.1
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
    manager = Mock()
    client.kill = manager.kill
    client.traj_stop = manager.traj_stop
    client.land = manager.land
    def mock_step(sample):
        if drone.status()["traj_state"] == 3: # EXECUTING
            return AbortDecision(level=1, reason="test abort")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    report = run_campaign(str(campaign_yaml), deps)
    expected_calls = [call.traj_stop(), call.land()]
    manager.assert_has_calls(expected_calls, any_order=False)
    assert not manager.kill.called
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
    assert report is not None
    deps.gate.on_flight_result.assert_not_called()
def test_d_level_3_abort(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    client.arm = Mock(wraps=client.arm)
    flight_count = 0
    in_traj = False
    def mock_step(sample):
        nonlocal flight_count, in_traj
        is_traj = drone.status()["traj_state"] == 3
        if is_traj and not in_traj:
            flight_count += 1
        in_traj = is_traj
        if flight_count == 2:
            return AbortDecision(level=3, reason="fatal")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    report = run_campaign(str(campaign_yaml), deps)
    assert report.status == "operator_needed"
    assert len(report.flights) == 2
    assert client.arm.call_count == 2
def test_e_cooldown(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.flight_timeout_s = 120.0
    deps.cooldown_min_s = 0.0
    orig_traj_start = client.traj_start
    def mock_traj_start():
        orig_traj_start()
        clock.sleep(30.0)
    client.traj_start = mock_traj_start
    takeoffs = []
    landings = []
    orig_takeoff = client.takeoff
    def mock_takeoff():
        takeoffs.append(clock.t)
        return orig_takeoff()
    client.takeoff = mock_takeoff
    orig_land = client.land
    def mock_land():
        orig_land()
        landings.append(clock.t)
    client.land = mock_land
    run_campaign(str(campaign_yaml), deps)
    assert len(takeoffs) >= 2
    duration_1 = landings[0] - takeoffs[0]
    wait_time = takeoffs[1] - landings[0]
    assert duration_1 > 5.0
    assert wait_time >= duration_1
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
    assert report is not None
    deps.gate.check_change.assert_called_with("+++ b/foo.py\n", {"justification": "test"}, {"foo.py": "code"})
def test_i_never_reaches_hover(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.flight_timeout_s = 0.01
    orig_land = client.land
    def mock_land():
        orig_land()
        drone._prim_state = 0
    client.land = mock_land
    client.kill = Mock(wraps=client.kill)
    report = run_campaign(str(campaign_yaml), deps)
    assert len(report.flights) > 0
    f = report.flights[0]
    assert f.abort_level == 1
    assert f.abort_reason.startswith("timeout")
    assert not client.kill.called
def test_j_consecutive_timeout_aborts(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.flight_timeout_s = 0.01
    orig_land = client.land
    def mock_land():
        orig_land()
        drone._prim_state = 0
    client.land = mock_land
    report = run_campaign(str(campaign_yaml), deps)
    assert len(report.flights) == 2
    for f in report.flights:
        assert f.abort_level == 1
    assert "timeout" in report.flights[0].abort_reason
    # the mock leaves z > 0, so the fake firmware refuses the next takeoff sequence: the runner says so
    assert "takeoff refused" in report.flights[1].abort_reason
    assert report.status == "operator_needed"
def test_k_tuner_history(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    histories = []
    def mock_propose(hist):
        histories.append(list(hist))
        return {"param": 1.0}
    deps.tuner.propose = Mock(side_effect=mock_propose)
    run_campaign(str(campaign_yaml), deps)
    assert len(histories) >= 3
    assert len(histories[0]) == 0
    assert len(histories[1]) == 1
    assert histories[1][0]["J"] == 1.23
    assert len(histories[2]) == 2
def test_l_runner_control_land(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.flight_timeout_s = 120.0
    
    client.kill = Mock(wraps=client.kill)
    client.traj_stop = Mock(wraps=client.traj_stop)
    client.land = Mock(wraps=client.land)
    
    def mock_step(sample):
        if drone.status()["traj_state"] == 3:
            deps.control.request("land")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    report = run_campaign(str(campaign_yaml), deps)
    
    client.traj_stop.assert_called_once()
    client.land.assert_called_once()
    assert not client.kill.called
    assert report.status == "operator_stop", f"Status was {report.status}, reason: {report.reason}"
    assert report.flights[0].abort_reason == "operator land"

def test_m_runner_control_abort(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    manager = Mock()
    client.kill = manager.kill
    
    def mock_step(sample):
        if drone.status()["traj_state"] == 3:
            deps.control.request("abort")
        return AbortDecision(level=0, reason="")
    deps.monitor.step = Mock(side_effect=mock_step)
    report = run_campaign(str(campaign_yaml), deps)
    
    assert report.status == "operator_needed"

def test_n_apply_params_rejection(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    deps.apply_params = Mock(return_value=False)
    
    report = run_campaign(str(campaign_yaml), deps)
    
    assert len(report.flights) == 0
    assert report.status == "operator_needed"
    assert report.reason == "param write refused"

def test_o_two_flight_judging_and_revert(tmp_path):
    campaign_yaml = tmp_path / "camp.yaml"
    campaign_yaml.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    
    deps.change_request = Mock(side_effect=[{"justification": "test"}] + [None] * 20)
    deps.gate.next_flight_must_hover.side_effect = [True] + [False] * 20
    deps.gate.on_flight_result.side_effect = ["pending", "revert"] + ["keep"] * 10
    
    report = run_campaign(str(campaign_yaml), deps)
    
    assert deps.gate.on_flight_result.call_count == 2
    deps.gate.on_flight_result.assert_has_calls([
        call(aborted=False, j=1.23, hover=True),
        call(aborted=False, j=1.23)
    ])
    
    assert deps.flash.call_count == 2
    
    assert report.flights[1].reflash_hash == "abc"


def test_p_landing_timeout_revert_flight(tmp_path):
    c = tmp_path / "camp.yaml"
    c.write_text(Path(YAML_PATH).read_text())
    drone = FakeDrone()
    drone.sbus_live = True
    client = WfbClient(drone.send)
    clock = FakeClock()
    deps = create_deps(drone, client, clock)
    deps.change_request = Mock(side_effect=[{"justification": "test"}] + [None] * 20)
    deps.gate.next_flight_must_hover.side_effect = [True] + [False] * 20
    deps.gate.on_flight_result.side_effect = ["pending", "revert"] + ["keep"] * 10
    deps.flight_timeout_s = 120.0
    lands = [0]
    orig_step = drone.step

    def stuck_airborne_step(dt):
        # From the second land on, the drone never touches down.
        if lands[0] >= 2:
            drone._z = 10.0
            drone._prim_state = 1
        orig_step(dt)
        if lands[0] >= 2:
            drone._z = 10.0
            drone._prim_state = 1
    drone.step = stuck_airborne_step
    orig_land = client.land

    def counting_land():
        orig_land()
        lands[0] += 1
    client.land = counting_land
    r = run_campaign(str(c), deps)
    assert deps.flash.call_count == 1
    assert r.status == "operator_needed"
    assert "landing timeout; revert flash pending" in r.reason
    assert r.flights[-1].reflash_hash == ""


FLY_YAML = """campaign: hover_ladder_t
objective: "hover ladder"
controller: pid
packs: [P4000-1]
max_flights: 9
mode: fly
experiments:
  - {name: hover_z050, scenario: hover, scenario_args: {z: 0.5}, capture: campaign, repeats: 1}
  - {name: hover_z070, scenario: hover, scenario_args: {z: 0.7}, capture: campaign, repeats: 1}
  - {name: hover_z130, scenario: hover, scenario_args: {z: 1.3}, capture: campaign, repeats: 1}
"""


def _fly_rig(tmp_path, health=None):
    path = tmp_path / "fly.yaml"
    path.write_text(FLY_YAML)
    drone = FakeDrone()
    drone.sbus_live = True
    clock = FakeClock()
    deps = create_deps(drone, WfbClient(drone.send), clock)
    deps.say = Mock()
    deps.ground_wait_s = 10.0
    if health is not None:
        deps.health = health
    return str(path), deps, clock


def test_fly_mode_flies_queue_once_with_one_go_and_no_tuning(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    report = run_campaign(path, deps)
    assert report.status == "complete", report.reason
    assert [f.experiment for f in report.flights] == ["hover_z050", "hover_z070", "hover_z130"]
    assert deps.wait_for_go.call_count == 1          # operator Go once; auto-next covers flights 2 and 3
    deps.tuner.propose.assert_not_called()
    deps.tuner.record.assert_not_called()
    deps.apply_params.assert_not_called()
    deps.change_request.assert_not_called()
    deps.gate.record_flight.assert_not_called()
    lines = [c.args[0] for c in deps.say.call_args_list]
    assert len(lines) == 3 and all(len(t.splitlines()) == 3 for t in lines)
    assert "auto-next OK -> flight 2/3 (hover_z070)" in lines[0]
    assert lines[2].endswith("campaign done")
    assert all(f.j == 1.23 for f in report.flights)


def test_fly_mode_failed_check_pauses_for_go(tmp_path):
    verdicts = iter([(False, "g_ekf_of_health = 0 (KF diverged)"), (True, ""), (True, "")])
    path, deps, clock = _fly_rig(tmp_path, health=lambda: next(verdicts))
    report = run_campaign(path, deps)
    assert report.status == "complete"
    assert deps.wait_for_go.call_count == 2          # first flight + the pause after flight 1
    first = deps.say.call_args_list[0].args[0]
    assert "PAUSED before flight 2/3: g_ekf_of_health = 0 (KF diverged)" in first


def test_fly_mode_pause_then_operator_stop(tmp_path):
    path, deps, clock = _fly_rig(tmp_path, health=lambda: (False, "drone is disarmed, not RC-armed"))
    deps.wait_for_go = Mock(side_effect=[True, False])
    report = run_campaign(path, deps)
    assert report.status == "operator_stop"
    assert len(report.flights) == 1


def test_fly_mode_waits_on_ground_before_next(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    t_land = []
    deps.on_flight = lambda rec: t_land.append(clock())
    starts = []
    real_takeoff = deps.client.takeoff
    deps.client.takeoff = lambda: (starts.append(clock()), real_takeoff())[1]
    run_campaign(path, deps)
    assert len(starts) == 3
    assert starts[1] - t_land[0] >= 10.0 - 1e-9


def _flight_drifts(tmp_path):
    """Per-flight worst-case origin walk (m) in the fly rig, from the runner's own start/landing clock."""
    from ground_station.service.campaign_runner import OF_DRIFT_CM_S
    path, deps, clock = _fly_rig(tmp_path)
    marks = []
    real_takeoff = deps.client.takeoff
    deps.client.takeoff = lambda: (marks.append(clock()), real_takeoff())[1]
    deps.on_flight = lambda rec: marks.append(clock())
    run_campaign(path, deps)
    return [(marks[i + 1] - marks[i]) * OF_DRIFT_CM_S / 100.0 for i in range(0, len(marks), 2)]


def test_fly_mode_drift_budget_pauses_for_pad_reseat(tmp_path):
    """The drone stays RC-armed between flights, so the optical-flow origin is not re-zeroed and the walk adds up:
    one flight's walk fits the budget, two do not, so the runner pauses for a re-seat before flight 3."""
    d = _flight_drifts(tmp_path)
    path, deps, clock = _fly_rig(tmp_path)
    deps.drift_budget_m = (max(d[0], d[1]) + d[0] + d[1]) / 2.0
    report = run_campaign(path, deps)
    assert report.status == "complete", report.reason
    assert deps.wait_for_go.call_count == 2          # first flight + the re-seat pause before flight 3
    lines = [c.args[0] for c in deps.say.call_args_list]
    assert "auto-next OK -> flight 2/3" in lines[0]
    assert "PAUSED before flight 3/3: re-seat" in lines[1] and "arming re-zeroes the origin" in lines[1]


def test_fly_mode_go_before_stops_for_the_operator_step(tmp_path):
    """A load swap needs the drone on the ground and an operator go: go_before turns auto-next into a pause."""
    path, deps, clock = _fly_rig(tmp_path)
    p = tmp_path / "fly.yaml"
    p.write_text(p.read_text().replace("{name: hover_z130,", "{name: hover_z130, go_before: mount the 250 g load on arm 1,"))
    report = run_campaign(path, deps)
    assert report.status == "complete", report.reason
    assert deps.wait_for_go.call_count == 2
    lines = [c.args[0] for c in deps.say.call_args_list]
    assert "auto-next OK -> flight 2/3" in lines[0]
    assert "PAUSED before flight 3/3: mount the 250 g load on arm 1" in lines[1]


def test_fly_mode_drift_budget_off_by_default(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    assert deps.drift_budget_m == 0.0
    run_campaign(path, deps)
    assert deps.wait_for_go.call_count == 1


def _quick_deps():
    drone = FakeDrone()
    drone.sbus_live = True
    return create_deps(drone, WfbClient(drone.send), FakeClock())


def test_wp23_fresh_pack_is_rested_and_pack_failures_name_the_pack():
    """A pack that has not flown in this campaign skips min_rest_s (before WP-23 the first flight waited
    min_rest_s after Go with the drone RC-armed on the pad); a refusal names the pack."""
    from ground_station.service.campaign_runner import PACK_RESTED_S
    deps = _quick_deps()
    deps.packs.next_flight_allowed.return_value = (False, "SOC: predicted post-flight SoC 12.0% < required 30.0%")
    report = run_campaign(YAML_PATH, deps)
    assert report.status == "operator_needed"
    assert report.reason == "pack P4000-1: SOC: predicted post-flight SoC 12.0% < required 30.0%"
    assert deps.packs.next_flight_allowed.call_args_list[0].args[2] == PACK_RESTED_S


def test_wp23_battery_not_streaming_is_a_readable_reason():
    deps = _quick_deps()
    deps.resting_v = Mock(side_effect=RuntimeError("battery voltage is not streaming (real_voltage)"))
    report = run_campaign(YAML_PATH, deps)
    assert (report.status, report.reason) == ("operator_needed",
                                              "battery: battery voltage is not streaming (real_voltage)")


def test_wp23_phases_and_arm_refused_reason():
    deps = _quick_deps()
    phases = []
    deps.on_phase = phases.append
    deps.arm_allowed = Mock(return_value=False)
    report = run_campaign(YAML_PATH, deps)
    assert (report.status, report.reason) == ("arm_refused", "stream check failed before takeoff")
    assert phases[0].startswith("waiting for go: pack P4000-1, flight 1/")
    assert phases[-1].startswith("stream check before flight 1/")
