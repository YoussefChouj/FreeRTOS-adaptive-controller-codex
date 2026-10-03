"""cli.py end to end on synthetic flights: propose, then verify (keep or revert)."""

import json
from dataclasses import replace

import pytest

from ground_station.autotune import cli, synth
from ground_station.autotune import excitation as ex
from ground_station.autotune.design import read_pid_rows

ROLL = read_pid_rows()["gyroxPID"]


@pytest.fixture(scope="module")
def proposal(tmp_path_factory):
    log = synth.write_session(tmp_path_factory.mktemp("id"), synth.simulate("roll", seed=0))
    rc = cli.main([str(log), "--axis", "roll"])
    return rc, log / "autotune_roll_rate.json"


def test_propose_prints_and_writes_knobs(proposal, capsys):
    rc, path = proposal
    assert rc == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "proposed" and data["member"] == "gyroxPID"
    assert data["baseline"]["hover_rate_rms_dps"] > 0
    by_sym = {k["symbol"]: k for k in data["knobs"]}
    assert by_sym and set(by_sym) <= {"gyroxPID.Kp", "gyroxPID.Kd"}
    for k in data["knobs"]:
        assert k["cmd_id"] == 0x01 and k["idx"] == 9 + ("Kp", "Ki", "Kd").index(k["symbol"].split(".")[1])
        assert 0.7 * k["prev"] - 1e-9 <= k["value"] <= 1.3 * k["prev"] + 1e-9


def test_verify_keeps_good_gains(proposal, tmp_path):
    data = json.loads(proposal[1].read_text(encoding="utf-8"))
    new = replace(ROLL, kp=data["proposed"]["Kp"], kd=data["proposed"]["Kd"])
    log = synth.write_session(tmp_path, synth.simulate("roll", rate=new, excite=ex.VERIFY_EXCITE, seed=0))
    assert cli.main([str(log), "--axis", "roll", "--verify", str(proposal[1])]) == 0
    assert json.loads((log / "autotune_roll_rate_verify.json").read_text())["decision"] == "keep"


def test_verify_reverts_bad_gains(proposal, tmp_path, capsys):
    data = json.loads(proposal[1].read_text(encoding="utf-8"))
    data["proposed"] = {"Kp": 6.5, "Ki": 0.01, "Kd": 5.0}  # low damping: PM falls well under 45 deg
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    log = synth.write_session(tmp_path / "v", synth.simulate("roll", rate=replace(ROLL, kp=6.5, kd=5.0),
                                                             excite=ex.VERIFY_EXCITE, seed=0))
    assert cli.main([str(log), "--axis", "roll", "--verify", str(bad)]) == 3
    out = capsys.readouterr().out
    assert "REVERT" in out and "margins below spec" in out
    knobs = json.loads((log / "autotune_roll_rate_verify.json").read_text())["knobs"]
    assert {k["symbol"]: k["value"] for k in knobs} == {k["symbol"]: k["prev"] for k in data["knobs"]}


def test_refusal_exits_2_with_reason(tmp_path, capsys):
    log = synth.write_session(tmp_path, synth.simulate("roll", excite={**ex.ID_EXCITE, "amp": 0.0}, seed=4))
    assert cli.main([str(log), "--axis", "roll"]) == 2
    assert "REFUSED: no multisine found" in capsys.readouterr().out
    assert json.loads((log / "autotune_roll_rate.json").read_text())["status"] == "refused"
