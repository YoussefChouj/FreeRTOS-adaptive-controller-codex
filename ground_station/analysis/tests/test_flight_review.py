"""WP-42 P5: flight_review.py builds one HTML page per recorded session."""
from __future__ import annotations

import math

from ground_station.analysis import flight_review as fr
from ground_station.analysis.tests.test_flight_debrief import _session
from ground_station.service import session_schema as ss
from ground_station.service.storage import CsvRecorder

SAT = (1900.0, 1100.0)                       # test-local limits, not the bench ones
HZ = 50.0


def _recorded(tmp_path, *, prim: bool = True):
    """A session written by the real recorder: IDLE 1 s, HOVER 8 s, IDLE 1 s at 50 Hz, one 100 ms frame gap in the
    tail. z 5 cm above its setpoint, x 2 cm off, a 12.5 Hz roll-rate sine, motor1 at 1950 for 2 s of the hold."""
    rec = CsvRecorder(tmp_path, enabled=True)
    block = ss.describe(build={"commit": "abc123def4567890", "dirty": True}, elf=None, variables=[])
    assert rec.start(label="p5", requested_by="agent:test", reason="review", session_schema=block)
    for i in range(int(10 * HZ)):
        t = i / HZ
        if 9.5 < t < 9.6:                    # drop four frames: one 100 ms gap
            continue
        hover = 1.0 <= t < 9.0
        motor = 1500.0 if hover else 1000.0
        vals = {"Ctrler.Z_posPID.FB": 0.55 if hover else 0.0, "Ctrler.Z_posPID.Des": 0.5 if hover else 0.0,
                "Ctrler.locxPID.FB": 2.0, "Ctrler.locxPID.Des": 0.0,
                "Ctrler.gyroxPID.FB": 10.0 * math.sin(2 * math.pi * 12.5 * t + 0.3), "Ctrler.gyroxPID.Des": 0.0,
                "imu_data.rol": 1.0, "imu_data.pit": -1.0,
                "mymotor.motor1": 1950.0 if 3.0 <= t < 5.0 else motor, "mymotor.motor2": motor,
                "mymotor.motor3": motor, "mymotor.motor4": motor}
        if prim:
            vals["g_wfb_status.prim_state"] = 2.0 if hover else 0.0
        rec.note(0, {f"slot0.{k}": v for k, v in vals.items()}, 1_000_000_000 + int(round(t * 1e9)))
    rec.stop()
    return rec.session_dir


def test_recorded_session_page(tmp_path):
    out = fr.review(_recorded(tmp_path), sat=SAT)
    assert out.name == "flight_review.html"
    page = out.read_text(encoding="utf-8")
    d = fr.page_data(out)

    assert d["hold_window_s"][0] == 1.0 and abs(d["hold_window_s"][1] - 8.98) < 1e-6
    track = {r["label"]: r for r in d["tracking"]}
    assert list(track)[:2] == ["x position", "z position"]
    assert abs(track["z position"]["err_rms"] - 0.05) < 1e-6 and track["z position"]["unit"] == "m"
    assert abs(track["x position"]["err_rms"] - 0.02) < 1e-6
    assert "Ctrler.gyroxPID" in track

    peak = {p["symbol"]: p for p in d["spectral_peaks"]}["Ctrler.gyroxPID.FB"]
    assert abs(peak["f_hz"] - 12.5) <= 0.25 and peak["fs_hz"] == 50.0

    sat = d["saturation"]
    assert 0.2 < sat["motor1"]["frac_hi"] < 0.3              # 2 s of the 8 s flight span
    assert all(sat[m]["frac_hi"] == 0 for m in ("motor2", "motor3", "motor4"))
    assert all(sat[m]["frac_lo"] == 0 for m in sat)          # ground idle (1000) is outside the span

    iv = d["intervals"]["0"]
    assert iv["median_ms"] == 20.0 and iv["gaps"] == 1 and iv["max_ms"] == 100.0

    assert page.count("data:image/png") == 4
    assert "abc123def456 (dirty)" in page and "p5 review" in page


def test_session_without_manifest_or_limits(tmp_path):
    out = fr.review(_session(tmp_path, "s1"), out=tmp_path / "r" / "page.html")
    d = fr.page_data(out)
    assert out == tmp_path / "r" / "page.html"
    assert d["sat_limits"] is None and d["build"] is None
    assert "frac_hi" not in d["saturation"]["motor1"]
    assert d["saturation"]["motor1"]["mean"] == 1500.0


def test_without_prim_state_the_span_is_the_whole_recording(tmp_path):
    d = fr.page_data(fr.review(_recorded(tmp_path, prim=False), sat=SAT))
    assert d["hold_window_s"] is None
    assert d["flight_span_s"][0] == 0.0 and abs(d["flight_span_s"][1] - 9.98) < 1e-6
    assert d["saturation"]["motor1"]["frac_lo"] > 0          # ground idle now counts


def test_stream_log_capture(tmp_path):
    rows = "\n".join(f"{1000 + 20 * i},0,{i},{0.1 * i},{-0.1 * i}" for i in range(50))
    (tmp_path / "cap.slot0.csv").write_text("t_src_ms,t_host_s,seq,Ctrler.Z_ratePID.Des,Ctrler.Z_ratePID.FB\n" + rows,
                                           encoding="utf-8")
    (tmp_path / "cap.slot1.csv").write_text("t_src_ms,t_host_s,seq,imu_data.pit\n" + "\n".join(
        f"{1000 + 40 * i},0,{i},{i % 3}" for i in range(25)), encoding="utf-8")
    out = fr.review(tmp_path / "cap.slot0.csv")
    assert out == tmp_path / "cap.flight_review.html"
    d = fr.page_data(out)
    assert [r["label"] for r in d["tracking"]] == ["Ctrler.Z_ratePID"]
    assert d["intervals"]["0"]["frames"] == 50 and d["intervals"]["1"]["median_ms"] == 40.0
