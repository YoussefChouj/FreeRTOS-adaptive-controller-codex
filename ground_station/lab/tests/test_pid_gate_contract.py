"""Offline regression for the real labday -> autotune boundary (no hardware)."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from ground_station.autotune import cli, synth
from ground_station.autotune.design import read_pid_rows
from ground_station.lab import labday


def _folder(tmp_path, monkeypatch, text, values):
    monkeypatch.setattr(labday, "ROOT", tmp_path)
    folder = tmp_path / "flight"
    folder.mkdir()
    session = tmp_path / "logs" / "sessions" / "recording"
    session.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"session_id": "recording"}))
    (folder / "gains.json").write_text(json.dumps({"values": values}))
    (folder / "saved.fly").write_text(text)
    return folder, session


def test_gate_calls_real_cli_with_recorded_gains_and_parses_result(tmp_path, monkeypatch):
    """Synthetic model proves the software interface only, not real flight performance."""
    row = read_pid_rows()["gyroxPID"]
    text = "campaign contract\ntakeoff 0.5\nhold 8\nexcite axis=roll signal=multisine f0=0.5 f1=15 amp=60 duration_s=30\nland\n"
    folder, session = _folder(tmp_path, monkeypatch, text, {str(i): v for i, v in zip((9, 10, 11), (row.kp, row.ki, row.kd))})
    synth.write_session(session, synth.simulate("roll", seed=0))
    # A stale cached fit must not bypass the repaired adapter.
    (folder / "autotune_roll_rate.json").write_text('{"status":"refused","reason":"stale"}')
    monkeypatch.setattr(cli, "save_diagnostics", lambda *a, **kw: None)
    result = labday.analyze_tune_gate(SimpleNamespace(gains={}), {},
                                    {"kind": "pid", "loop": "rate", "axes": ["roll"]}, [folder])
    assert not result["refusal_reasons"]
    assert set(result["proposals"]) == {9, 10, 11}
    assert result["coherence"] >= 0.6 and result["residual"] <= 0.35
    assert result["margins"]["pm_deg"] > 0
    assert result["margins"]["crossover_rad_s"] > 0
    data = json.loads((folder / "autotune_roll_rate.json").read_text())
    assert data["gains_in_force"] == row.gains()
    assert data["logs"] == [str(session)]


def test_cli_extracts_both_saved_bands(tmp_path):
    low = dict(signal="multisine", f0=0.2, f1=5, amp=45, duration_s=30)
    high = dict(signal="multisine", f0=2, f1=40, amp=60, duration_s=30)
    a = synth.simulate("roll", excite=low, seed=0)
    b = synth.simulate("roll", excite=high, seed=1)
    b["received_ns"] += 60_000_000_000
    b.loc[b["key"].str.endswith("xTickCount"), "value"] += 60_000
    session = synth.write_session(tmp_path / "session", pd.concat([a, b]))
    fly = tmp_path / "saved.fly"
    fly.write_text("campaign split\ntakeoff 0.5\nhold 8\n"
                   "excite axis=roll signal=multisine f0=0.2 f1=5 amp=45 duration_s=30\nhold 3\n"
                   "excite axis=roll signal=multisine f0=2 f1=40 amp=60 duration_s=30\nland\n")
    rc = cli.main([str(session), "--axis", "roll", "--flight-file", str(fly)])
    data = json.loads((session / "autotune_roll_rate.json").read_text())
    assert rc in (0, 2)  # quality refusal is an allowed outcome, a parser crash is not
    assert data["excitations"] == [low, high]
    assert len(data["starts_s"]) == 2
    assert data["starts_s"][0] == pytest.approx(9, abs=0.1)
    assert data["starts_s"][1] == pytest.approx(69, abs=0.1)
    assert data["fit"]["bins"] >= 4


def test_gate_fails_closed_on_bad_snapshot_or_parser_exit(tmp_path, monkeypatch):
    folder, session = _folder(tmp_path, monkeypatch, "unused", {"9": 3.5, "10": 0.01, "11": 13})
    (session / "telemetry.csv").touch()
    monkeypatch.setattr(cli, "main", lambda argv: (_ for _ in ()).throw(SystemExit(2)))
    result = labday.analyze_tune_gate(SimpleNamespace(gains={}), {},
                                    {"kind": "pid", "loop": "rate", "axes": ["roll"]}, [folder])
    assert not result["proposals"]
    assert any("failed (2)" in r for r in result["refusal_reasons"])
    (folder / "gains.json").unlink()
    result = labday.analyze_tune_gate(SimpleNamespace(gains={}), {},
                                    {"kind": "pid", "loop": "rate", "axes": ["roll"]}, [folder])
    assert not result["proposals"] and result["refusal_reasons"]
