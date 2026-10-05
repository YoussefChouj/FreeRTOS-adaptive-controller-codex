"""twin_compare: flights pair by experiment and repeat order; recording extras read the HOVER window only."""
from __future__ import annotations

import csv
import json

from ground_station.analysis import twin_compare
from ground_station.service.campaign_runner import PRIM_HOVER


def _session(path, zrate_err, u_ad):
    path.mkdir()
    with (path / "telemetry.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["received_ns", "slot", "key", "value"])
        for i in range(30):
            ns = i * 100_000_000
            hover = 5 <= i < 25
            rows = {"g_wfb_status.prim_state": PRIM_HOVER if hover else 0,
                    "Ctrler.Z_ratePID.Des": 0.0, "Ctrler.Z_ratePID.FB": zrate_err if hover else 99.0,
                    "mrac_state.z_rate.u_ad": u_ad if hover else -500.0}
            w.writerows([ns, 0, f"slot0.{k}", v] for k, v in rows.items())
    return str(path)


def _campaign(path, name, flights):
    path.mkdir()
    (path / "metrics.json").write_text(json.dumps({"campaign": name, "stamp": "s", "status": "done",
                                                   "flights": flights}), encoding="utf-8")
    return path


def _flight(fid, exp, z_rms, rec=""):
    return {"flight_id": fid, "experiment": exp, "recording": rec,
            "metrics": {"hold_s": 20.0, "z": {"err_rms_m": z_rms}}}


def test_pairs_by_experiment_and_repeat_and_reads_hover_window(tmp_path, capsys):
    ra = _session(tmp_path / "ra", 0.2, 0.0)
    rb = _session(tmp_path / "rb", 0.05, 150.0)
    a = _campaign(tmp_path / "pid", "asym_load_pid",
                  [_flight("F1", "pad_hover", 0.04, ra), _flight("F2", "pad_hover", 0.05), _flight("F3", "arm_hover", 0.03)])
    b = _campaign(tmp_path / "mrac", "asym_load_mrac",
                  [_flight("G1", "pad_hover", 0.02, rb), _flight("G2", "noload_hover", 0.01)])
    assert twin_compare.main([str(a), str(b), "--json", str(tmp_path / "o.json")]) == 0
    pairs = {p["pair"]: p for p in json.loads((tmp_path / "o.json").read_text())}
    assert list(pairs) == ["pad_hover#1", "pad_hover#2", "arm_hover#1", "noload_hover#1"]
    rows = {r["metric"]: r for r in pairs["pad_hover#1"]["rows"]}
    assert rows["z err rms [m]"]["b_minus_a"] == -0.02
    assert rows["zrate err rms"]["a"] == 0.2 and rows["zrate err rms"]["b"] == 0.05      # outside-HOVER 99 ignored
    assert rows["u_ad_z mean"]["b"] == 150.0 and rows["u_ad_z max"]["b"] == 150.0        # -500 pre-HOVER ignored
    assert pairs["pad_hover#2"]["b"] is None and pairs["noload_hover#1"]["a"] is None
    out = capsys.readouterr().out
    assert "### pad_hover#1  (A F1, B G1)" in out and "| u_ad_z mean | 0 | 150 | 150 |" in out
