"""lab_flights on a synthetic recording: 40 cm x step with a known 5 cm overshoot, then a 1 s landing."""
import numpy as np
import pytest
import pandas as pd

from ground_station.analysis import lab_flights as L


def _session(tmp_path, gap=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = np.arange(0, 30, 0.04)
    tau = t - 10
    xd = np.where(tau >= 0, 40.0, 0.0)                                  # cm, as the firmware streams it
    x = np.where(tau >= 0, 40 * np.clip(tau / 0.5, 0, 1) + 5 * np.exp(-((tau - 1) / 0.3) ** 2), 0.0)
    zd = np.interp(t, [0, 2, 3, 25, 26, 30], [0, 0, 0.5, 0.5, 0, 0])   # m
    keys = {"Ctrler.locxPID.Des": xd, "Ctrler.locxPID.FB": x, "Ctrler.locyPID.Des": 0 * t,
            "Ctrler.locyPID.FB": 0 * t, "Ctrler.Z_posPID.Des": zd, "Ctrler.Z_posPID.FB": zd,
            "Ctrler.Z_ratePID.FB": np.gradient(zd, t)}
    rows = [(int(ti * 1e9) + 1, 1, k, v[i]) for i, ti in enumerate(t) for k, v in keys.items()
            if gap is None or not gap[0] < ti < gap[1]]
    pd.DataFrame(rows, columns=["received_ns", "slot", "key", "value"]).to_csv(tmp_path / "telemetry.csv", index=False)
    return tmp_path


def test_step_overshoot_and_landing(tmp_path):
    w = L.load(_session(tmp_path))
    assert abs(w["Ctrler.locxPID.FB"].max() - 0.45) < 1e-6           # cm converted to m
    r = L.xy_metrics(w)
    assert abs(r["overshoot_x_cm"] - 5.0) < 0.2
    assert abs(r["t_step"] - 10) < 0.05
    land = L.land_metrics(w)
    assert abs(land["descent_s"] - 0.9) < 0.15                        # z target leaves 0.47 m at 25.06 s, z < 6 cm at ~25.9 s
    assert land["err_at_touch_cm"] < 0.5 and land["target_xy_cm"][0] == 40


@pytest.mark.filterwarnings("error::RuntimeWarning")
def test_no_step_scored_across_a_telemetry_gap(tmp_path):
    """10-21 smoke and ground-effect: no samples in the step hold window used to warn "Mean of empty slice"."""
    r = L.xy_metrics(L.load(_session(tmp_path, gap=(10.3, 20.5))))
    assert "overshoot_x_cm" not in r and abs(r["airborne_s"]) > 0


def test_is_sim_detection(tmp_path):
    s1 = tmp_path / "s1"
    s1.mkdir()
    (s1 / "manifest.json").write_text('{"sim": true}', encoding="utf-8")
    assert L.is_sim(s1) is True

    s2 = tmp_path / "s2"
    s2.mkdir()
    (s2 / "manifest.json").write_text('{"sim": false}', encoding="utf-8")
    assert L.is_sim(s2) is False

    s3 = tmp_path / "sim-20261015-run"
    assert L.is_sim(s3) is True

    df_sim = pd.DataFrame({
        "mymotor.motor1": [3000.0] * 10,
        "mymotor.motor2": [3000.0] * 10,
        "mymotor.motor3": [3000.0] * 10,
        "mymotor.motor4": [3000.0] * 10,
        "Ctrler.locxPID.Des": [0.0] * 10,
        "Ctrler.locxPID.FB": [0.0] * 10,
    })
    assert L.is_sim(w=df_sim) is True

    df_real = pd.DataFrame({
        "mymotor.motor1": [3050.0] * 10,
        "mymotor.motor2": [3120.0] * 10,
        "mymotor.motor3": [3080.0] * 10,
        "mymotor.motor4": [3110.0] * 10,
        "Ctrler.locxPID.Des": [0.0] * 10,
        "Ctrler.locxPID.FB": [0.0] * 10,
    })
    assert L.is_sim(w=df_real) is False


def test_camera_touchdown_offset_and_stale_override(tmp_path):
    w = L.load(_session(tmp_path / "sess"))
    w["g_ext_pos.e_x"] = 2.0
    w["g_ext_pos.e_y"] = -1.0
    w["g_ext_pos.stale"] = 0.0

    land = L.land_metrics(w)
    assert land["cam_stale"] is False
    assert land["cam_source"] == "telemetry"
    assert abs(land["cam_touch_xy_cm"][0] - (land["touch_xy_cm"][0] + 2.0)) < 1e-4
    assert abs(land["cam_touch_xy_cm"][1] - (land["touch_xy_cm"][1] - 1.0)) < 1e-4
    assert land["primary_source"] == "ekf"

    fixes_file = tmp_path / "fixes.csv"
    fixes_file.write_text(
        "t_send,t_cap,pad_x_m,pad_y_m,earth_x_cm,earth_y_cm,frame_hex\n"
        + "".join(f"1.0,1.0,0.40,0.00,41.0,-0.5,ccdd\n" for _ in range(20)),
        encoding="utf-8"
    )
    w["g_ext_pos.stale"] = 1.0
    sess_with_fixes = tmp_path / "sess_fixes"
    sess_with_fixes.mkdir()
    (sess_with_fixes / "fixes.csv").write_text(fixes_file.read_text(encoding="utf-8"), encoding="utf-8")

    land_stale = L.land_metrics(w, session=sess_with_fixes)
    assert land_stale["cam_stale"] is True
    assert land_stale["cam_source"] == "fixes.csv"
    assert land_stale["primary_source"] == "fixes.csv"
    assert abs(land_stale["cam_touch_xy_cm"][0] - 41.0) < 1e-4
    assert abs(land_stale["cam_touch_xy_cm"][1] - (-0.5)) < 1e-4
    assert abs(land_stale["cam_err_at_touch_cm"] - float(np.hypot(1.0, 0.5))) < 1e-3
    assert land_stale["primary_err_at_touch_cm"] == land_stale["cam_err_at_touch_cm"]


def test_main_include_sim_flag(tmp_path, monkeypatch):
    import json
    date_str = "2026-10-99"
    flights_dir = tmp_path / "logs" / "flights" / date_str
    flights_dir.mkdir(parents=True)

    f1_dir = flights_dir / "01_pad-landing-real"
    f1_dir.mkdir()
    s1_dir = tmp_path / "logs" / "sessions" / "s_real"
    s1_dir.mkdir(parents=True)
    _session(s1_dir)
    (s1_dir / "manifest.json").write_text(json.dumps({"sim": False}), encoding="utf-8")
    (f1_dir / "debrief.txt").write_text(f"complete\noutputs {tmp_path / 'camp1'}\n", encoding="utf-8")
    (tmp_path / "camp1").mkdir()
    (tmp_path / "camp1" / "summary.md").write_text(f"recording `{s1_dir.relative_to(tmp_path)}`", encoding="utf-8")

    f2_dir = flights_dir / "02_pad-landing-sim"
    f2_dir.mkdir()
    s2_dir = tmp_path / "logs" / "sessions" / "s_sim"
    s2_dir.mkdir(parents=True)
    _session(s2_dir)
    (s2_dir / "manifest.json").write_text(json.dumps({"sim": True}), encoding="utf-8")
    (f2_dir / "debrief.txt").write_text(f"complete\noutputs {tmp_path / 'camp2'}\n", encoding="utf-8")
    (tmp_path / "camp2").mkdir()
    (tmp_path / "camp2" / "summary.md").write_text(f"recording `{s2_dir.relative_to(tmp_path)}`", encoding="utf-8")

    monkeypatch.setattr(L, "ROOT", tmp_path)

    out_default = tmp_path / "out_default"
    L.main([date_str, "--out", str(out_default)])
    metrics_default = json.loads((out_default / "metrics.json").read_text(encoding="utf-8"))
    assert "01_pad-landing-real" in metrics_default
    assert "02_pad-landing-sim" not in metrics_default

    out_sim = tmp_path / "out_sim"
    L.main([date_str, "--include-sim", "--out", str(out_sim)])
    metrics_sim = json.loads((out_sim / "metrics.json").read_text(encoding="utf-8"))
    assert "01_pad-landing-real" in metrics_sim
    assert "02_pad-landing-sim" in metrics_sim
