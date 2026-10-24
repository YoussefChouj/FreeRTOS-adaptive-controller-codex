"""The `.fly` ground verbs (layers / xy_layers, mrac_ref, mrac_prior) and the `step` / `pattern` steps.

Every number in these tests is either from the firmware's own table (the host mirror in
ground_station/analysis/mrac_variants.py) or PROPOSED and marked as such. No invented bandwidth reaches the drone:
a missing fit file refuses the flight.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ground_station.analysis.mrac_variants import VARIANT_FIELDS
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service import fly_file, ground_plan
from ground_station.service.campaign_runner import fly_scenario
from ground_station.service.fake_drone_mrac import MrcFakeDrone
from ground_station.service.scenario_schema import ScenarioError, parse_scenario
from ground_station.service.tests.test_fly_scenario import _rig
from ground_station.service.tests.test_runner import FakeClock, create_deps

XY_LAYERS_FIELD = [name for name, _lo, _hi in VARIANT_FIELDS].index("xy_layers")
FIELD_NO = {name: i for i, (name, _lo, _hi) in enumerate(VARIANT_FIELDS)}


def _mrc_rig():
    """The rig of test_fly_scenario, but with the MRAC-aware fake drone.

    The MRAC ground writes (CMD 0x1D / 0x2F..0x34) are answered by the host mirror of the firmware's ground
    path (ground_station/service/fake_drone_mrac.py), so the runner test flies the real verb sequence.
    """
    drone = MrcFakeDrone()
    clock = FakeClock()
    deps = create_deps(drone, WfbClient(drone.send), clock)
    return drone, clock, deps


def _scenario(text: str) -> dict:
    data, _ = fly_file.compile_fly(text, "t.fly")
    return data["experiments"][0]["scenario"]


# --------------------------------------------------------------------------- layers


def test_layers_xy_both_is_one_cmd_1d_write_on_the_takeoff():
    steps = _scenario("layers xy both\nhold 3\n")["steps"]
    assert steps[0]["takeoff"]["ground"] == [
        {"kind": "variant", "axis": 0, "field": XY_LAYERS_FIELD, "value": 3.0}
    ]


def test_layers_xy_layers_is_the_alias_of_layers_xy():
    a = _scenario("layers xy outer\nhold 3\n")["steps"][0]["takeoff"]["ground"]
    b = _scenario("xy_layers outer\nhold 3\n")["steps"][0]["takeoff"]["ground"]
    assert a == b == [{"kind": "variant", "axis": 0, "field": XY_LAYERS_FIELD, "value": 2.0}]


@pytest.mark.parametrize("target,value", [("inner", 1.0), ("outer", 2.0), ("both", 3.0), ("off", 0.0)])
def test_layers_targets_are_the_firmware_layer_bits(target, value):
    steps = _scenario(f"layers xy {target}\nhold 3\n")["steps"]
    assert steps[0]["takeoff"]["ground"][0]["value"] == value


def test_layers_group_without_a_contract_field_is_refused_with_the_all_loop_hint():
    # att / yaw / z layer fields come with the all-loop firmware; the contract does not carry them yet.
    # The refusal is loud and names the field: the flight stays in the deck (it parses), it does not take off.
    write = _scenario("layers att both\nhold 3\n")["steps"][0]["takeoff"]["ground"][0]
    assert write["kind"] == "refuse"
    assert write["field"] == "att_layers"
    assert "needs mrac-all-loops item 0" in write["reason"]
    assert ground_plan.write_label(write).startswith("REFUSED att_layers")


def test_a_missing_field_refuses_only_its_flight_at_run_time():
    # Never drop a flight: the deck parses, and on the fake drone the flight is refused on the ground, unsent.
    _, _, deps = _mrc_rig()
    sent = []
    deps.client.mrac_variant = lambda *a, **k: sent.append(a) or True
    out = fly_scenario(parse_scenario(_scenario("layers att both\nhold 2\n")), deps)
    assert out.aborted
    assert out.decision.reason.startswith("takeoff refused at REFUSED att_layers")
    assert sent == []


def test_layers_unknown_group_or_target_is_refused():
    with pytest.raises(fly_file.FlyFileError, match="unknown layer group"):
        _scenario("layers spin both\nhold 3\n")
    with pytest.raises(fly_file.FlyFileError, match="target must be one of"):
        _scenario("layers xy middle\nhold 3\n")


def test_layers_keeps_the_script_order_of_several_ground_verbs():
    steps = _scenario("layers xy both\nlayers xy inner\nhold 3\n")["steps"]
    assert [g["value"] for g in steps[0]["takeoff"]["ground"]] == [3.0, 1.0]


# --------------------------------------------------------------------------- mrac_ref


def _write_fit(tmp_path: Path, kind: str, payload: dict) -> Path:
    d = tmp_path / "logs" / "ref_fit"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{kind}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_mrac_ref_writes_the_fitted_x_y_reference_per_axis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_fit(tmp_path, "lqr", {"order": "1st", "ref_model_bw_x": 4.8, "ref_model_bw_y": 4.4,
                                  "ref_delay_s_x": 0.01, "ref_delay_s_y": 0.01})
    steps = _scenario("mrac_ref from=lqr\nhold 3\n")["steps"]
    writes = steps[0]["takeoff"]["ground"]
    assert [(w["axis"], w["field"], w["value"]) for w in writes] == [
        (4, FIELD_NO["ref_type"], 1.0),
        (4, FIELD_NO["ref_model_bw"], 4.8),
        (4, FIELD_NO["ref_delay_s"], 0.01),
        (5, FIELD_NO["ref_type"], 1.0),
        (5, FIELD_NO["ref_model_bw"], 4.4),
        (5, FIELD_NO["ref_delay_s"], 0.01),
    ]


def test_mrac_ref_without_a_fit_file_refuses_the_flight(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    writes = _scenario("mrac_ref from=lqr\nhold 3\n")["steps"][0]["takeoff"]["ground"]
    assert writes[0]["kind"] == "refuse"
    assert writes[0]["field"] == "ref_fit:lqr"
    assert "no fitted reference" in writes[0]["reason"]


def test_mrac_ref_without_a_fit_file_refuses_at_run_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, _, deps = _mrc_rig()
    sent = []
    deps.client.mrac_variant = lambda *a, **k: sent.append(a) or True
    out = fly_scenario(parse_scenario(_scenario("mrac_ref from=lqr\nhold 2\n")), deps)
    assert out.aborted
    assert out.decision.reason.startswith("takeoff refused at REFUSED ref_fit:lqr")
    assert sent == []


def test_mrac_ref_refuses_a_value_outside_the_firmware_range(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_fit(tmp_path, "lqr", {"order": "1st", "ref_model_bw_x": 900.0, "ref_model_bw_y": 4.4,
                                  "ref_delay_s_x": 0.0, "ref_delay_s_y": 0.0})
    with pytest.raises(fly_file.FlyFileError, match="outside the firmware"):
        _scenario("mrac_ref from=lqr\nhold 3\n")


def test_mrac_ref_second_order_needs_the_zeta_field(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_fit(tmp_path, "mpc", {"order": "2nd", "ref_model_bw_x": 4.4, "ref_model_bw_y": 4.4,
                                  "ref_delay_s_x": 0.0, "ref_delay_s_y": 0.0,
                                  "ref_model_zeta_x": 1.0, "ref_model_zeta_y": 1.0})
    if ground_plan.field_number("ref_model_zeta") is not None:
        writes = _scenario("mrac_ref from=mpc\nhold 3\n")["steps"][0]["takeoff"]["ground"]
        assert (5, ground_plan.field_number("ref_model_zeta"), 1.0) in [
            (w["axis"], w["field"], w["value"]) for w in writes]
    else:
        # The field is missing: the refusal names it, loudly. Never a skip.
        with pytest.raises(fly_file.FlyFileError, match="needs mrac-all-loops item 0"):
            _scenario("mrac_ref from=mpc\nhold 3\n")
        with pytest.raises(fly_file.FlyFileError, match="ref_model_zeta"):
            _scenario("mrac_ref from=mpc\nhold 3\n")


# --------------------------------------------------------------------------- mrac_prior


def test_mrac_prior_needs_a_session_with_theta_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fly_file.FlyFileError, match="no session dir"):
        _scenario("mrac_prior from=20261021-193653\nhold 3\n")


def test_mrac_prior_reads_the_theta_columns_of_a_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "logs" / "sessions" / "s1"
    d.mkdir(parents=True)
    rows = ["received_ns,slot,key,value"]
    for i in range(3):
        rows.append(f"{i * 10},1,mrac_state.pitch.Theta[0],{0.5 + i * 0.1}")
        rows.append(f"{i * 10},1,mrac_state.pitch.Theta[1],0.25")
    (d / "telemetry.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    writes = _scenario("mrac_prior from=s1\nhold 3\n")["steps"][0]["takeoff"]["ground"]
    # The median of the last samples: Theta[0] -> 0.6 (median of 0.5, 0.6, 0.7), Theta[1] -> 0.25.
    assert writes[0] == {"kind": "prior", "axis": "pitch", "idx": 0, "value": pytest.approx(0.6)}
    assert writes[1] == {"kind": "prior", "axis": "pitch", "idx": 1, "value": pytest.approx(0.25)}
    # The warm bit of the pitch axis comes after every element, so the firmware re-seeds with the whole prior.
    warm = [w for w in writes if w["idx"] == 0xF2 and w["axis"] == "pitch"]
    assert warm and warm[0]["value"] == 1.0
    assert writes.index(warm[0]) > 1


def _session(root: Path, name: str, theta: float) -> None:
    d = root / "logs" / "sessions" / name
    d.mkdir(parents=True)
    rows = ["received_ns,slot,key,value", f"0,1,mrac_state.pitch.Theta[0],{theta}"]
    (d / "telemetry.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_mrac_prior_latest_takes_the_newest_session_of_that_campaign(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _session(tmp_path, "20261024-101500-lab-p-pattern-learn-001_f05_lab_p_pattern_learn", 0.1)
    _session(tmp_path, "20261024-113000-lab-p-pattern-learn-002_f09_lab_p_pattern_learn", 0.2)
    # A longer campaign name that starts with the same words is a different campaign: never picked.
    _session(tmp_path, "20261024-120000-lab-p-pattern-learn-x-001", 0.9)
    (tmp_path / "logs" / "sessions" / "20261024-130000-lab-p-pattern-learn-003").mkdir()  # no telemetry.csv
    writes = _scenario("mrac_prior from=latest:lab-p-pattern-learn\nhold 3\n")["steps"][0]["takeoff"]["ground"]
    assert writes[0] == {"kind": "prior", "axis": "pitch", "idx": 0, "value": pytest.approx(0.2)}


def test_mrac_prior_latest_without_a_flown_session_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    writes = _scenario("mrac_prior from=latest:lab-p-pattern-learn\nhold 3\n")["steps"][0]["takeoff"]["ground"]
    assert writes[0]["kind"] == "refuse"
    assert writes[0]["field"] == "prior:latest:lab-p-pattern-learn"
    assert "fly it first" in writes[0]["reason"]


def test_mrac_prior_latest_needs_a_campaign_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fly_file.FlyFileError, match="expected a campaign name"):
        _scenario("mrac_prior from=latest:../x\nhold 3\n")


# --------------------------------------------------------------------------- pattern


def test_pattern_compiles_comma_separated_axes():
    steps = _scenario("hold 3\npattern layer=outer speed=slow axes=x,y periods=1\n")["steps"]
    assert steps[2]["pattern"] == {"layer": "outer", "speed": "slow", "axes": ["x", "y"], "periods": 1}


# --------------------------------------------------------------------------- step


def test_step_compiles_to_a_program_with_the_firmware_preview():
    steps = _scenario("step axis=z delta=0.15 hold=4\n")["steps"]
    assert steps[1]["step"] == {"axis": "z", "delta": 0.15, "hold": 4.0}


def test_step_yaw_is_accepted():
    steps = _scenario("step axis=yaw delta=30 hold=3\n")["steps"]
    assert steps[1]["step"]["axis"] == "yaw"


def test_step_refuses_what_the_firmware_could_not_fly():
    with pytest.raises(ScenarioError) as e:
        parse_scenario({"hover_z_m": 0.5, "steps": [
            {"takeoff": {"z": 0.5}}, {"hold": {"s": 2}}, {"step": {"axis": "x", "delta": 0.1, "hold": 2}}]})
    assert e.value.problems  # the schema reports its own problems


def test_step_out_of_range_is_refused_by_the_schema():
    with pytest.raises(ScenarioError) as e:
        parse_scenario({"hover_z_m": 0.5, "steps": [
            {"takeoff": {"z": 0.5}}, {"hold": {"s": 2}},
            {"step": {"axis": "z", "delta": 5.0, "hold": 2.0}}]})
    assert any("must be in" in p for p in e.value.problems), e.value.problems


# --------------------------------------------------------------------------- the runner


def test_ground_writes_reach_the_fake_drone_before_takeoff():
    drone, _, deps = _mrc_rig()
    text = "layers xy both\nhold 2\n"
    out = fly_scenario(parse_scenario(_scenario(text)), deps)
    assert not out.aborted and out.landed, out.decision
    # The layer target the firmware flies is the one the verb wrote.
    assert drone.xy_layers_live == 3


def test_a_refused_ground_write_aborts_on_the_ground():
    drone, _, deps = _mrc_rig()
    seen = []

    def _boom(axis, field, value):
        seen.append((axis, field, value))
        return False

    deps.client.mrac_variant = _boom
    text = "layers xy both\nhold 2\n"
    out = fly_scenario(parse_scenario(_scenario(text)), deps)
    assert out.aborted
    assert seen  # the write was attempted


def test_a_lost_ground_write_reply_is_resent():
    """The lossy-link rule: an idempotent absolute write may be resent (up to 3 tries)."""
    drone, _, deps = _mrc_rig()
    calls = []

    def _flaky(axis, field, value):
        calls.append(1)
        if len(calls) == 1:
            deps.client.last_error = "cmd 0x1D: no result in 1.0 s"
            return False
        deps.client.last_error = ""
        return True

    deps.client.mrac_variant = _flaky
    text = "layers xy both\nhold 2\n"
    out = fly_scenario(parse_scenario(_scenario(text)), deps)
    assert not out.aborted and out.landed, out.decision
    assert len(calls) == 2


# --------------------------------------------------------------------------- ground_plan unit


def test_check_write_rejects_a_value_outside_the_firmware_range():
    with pytest.raises(ground_plan.GroundWriteError, match="outside"):
        ground_plan.check_write({"kind": "variant", "axis": 4, "field": FIELD_NO["ref_model_bw"], "value": 900.0})


def test_check_write_rejects_an_unknown_field():
    with pytest.raises(ground_plan.GroundWriteError, match="not one of the firmware"):
        ground_plan.check_write({"kind": "variant", "axis": 4, "field": 999, "value": 1.0})


def test_write_label_names_the_axis_and_the_field():
    w = {"kind": "variant", "axis": 4, "field": FIELD_NO["ref_model_bw"], "value": 4.8}
    assert "x" in ground_plan.write_label(w) and "ref_model_bw" in ground_plan.write_label(w)


# --------------------------------------------------------------------------- the field numbers, looked up by name


def test_the_field_numbers_come_from_the_contract_by_name(monkeypatch):
    """The contract is the source of truth for the field numbers, and it is read, never written.

    A fake contract entry stands in for the firmware agent's build: the host must use the number the
    contract names, without any host change and without the number being typed in the host.
    """
    fc = ground_plan._contract_module()

    fake = {"att_layers": 32, "yaw_layers": 33, "z_layers": 34}
    monkeypatch.setattr(fc, "MRAC_LAYER_FIELDS", fake, raising=False)
    assert ground_plan.field_number("att_layers") == 32
    assert ground_plan.field_number("yaw_layers") == 33
    assert ground_plan.layer_field("att") == 32
    # The xy group stays on the host mirror (the firmware table already carries it).
    assert ground_plan.layer_field("xy") == XY_LAYERS_FIELD


def test_a_contract_named_field_is_used_by_the_layers_verb(monkeypatch):
    fc = ground_plan._contract_module()

    monkeypatch.setattr(fc, "MRAC_LAYER_FIELDS", {"att_layers": 32}, raising=False)
    write = _scenario("layers att both\nhold 3\n")["steps"][0]["takeoff"]["ground"][0]
    # Field 32+ does not fit the 5-bit field of axes 0-3, so the layer write goes on axis x (idx 0x80 | field << 1).
    assert write == {"kind": "variant", "axis": 4, "field": 32, "value": 3.0}


class _RecordingClient:
    """A client that records the raw CMD 0x1D sends and applies each one."""

    last_error = ""

    def __init__(self):
        self.sent = []

    def _send_cmd(self, cmd, idx, value):
        self.sent.append((cmd, idx, value))
        return True


def test_a_field_beyond_the_mirror_is_sent_with_the_axis_x_idx(monkeypatch):
    # att / yaw / z layers (33 / 34 / 35) reach the firmware as idx 0xC2 / 0xC4 / 0xC6 (API/mrac.c MRAC_VariantIdxSet).
    fc = ground_plan._contract_module()
    monkeypatch.setattr(fc, "MRAC_LAYER_FIELDS", {"att_layers": 33, "yaw_layers": 34, "z_layers": 35}, raising=False)
    client = _RecordingClient()
    for group, idx in (("att", 0xC2), ("yaw", 0xC4), ("z", 0xC6)):
        write = ground_plan.layer_write(group, "both")
        assert write == {"kind": "variant", "axis": 4, "field": ground_plan.layer_field(group), "value": 3.0}
        assert ground_plan.send_write(client, write) is True
        assert client.sent[-1] == (0x1D, idx, 3.0)


def test_the_layer_fields_come_from_the_host_mirror_rows(monkeypatch):
    # After the all-loop merge the host mirror carries att / yaw / z_layers at 33 / 34 / 35; no contract entry needed.
    from ground_station.analysis import mrac_variants as mv

    fc = ground_plan._contract_module()
    monkeypatch.setattr(fc, "MRAC_LAYER_FIELDS", {}, raising=False)
    rows = list(mv.VARIANT_FIELDS)
    while len(rows) < 33:
        rows.append((f"pad_{len(rows)}", 0.0, 1.0))
    rows += [("att_layers", 0.0, 3.0), ("yaw_layers", 0.0, 3.0), ("z_layers", 0.0, 3.0)]
    monkeypatch.setattr(mv, "VARIANT_FIELDS", rows)
    assert ground_plan.layer_fields()["att"] == 33
    assert ground_plan.layer_fields()["z"] == 35
    assert ground_plan.layer_write("yaw", "outer") == {"kind": "variant", "axis": 4, "field": 34, "value": 2.0}
