"""Campaign-session telemetry.csv read like the stream_log slot CSVs (2026-10-19): one synthetic flight written both
ways gives the same read_slots, fixes_clock, nn4_data and r4_truth output."""
import json

import numpy as np
import pandas as pd
import pytest

from ground_station.analysis import log_corpus, video_truth as vt
from ground_station.analysis.tests import test_nn4_data as nn

T0_NS = 1_760_000_000_000_000_000
CAM = {"K": np.eye(3).tolist(), "dist": [0.0] * 5}


def on_wire(df):
    """What the frame carries: wifi_bridge rounds every value to 6 decimals, the frame time to whole ms."""
    out = df.round(6)
    out["t_src_ms"] = np.round(df["t_src_ms"].to_numpy(float))
    return out


def write_session(path, lanes, batch=4, grouped=False, meta=True, aliases=True):
    """lanes {capture lane: DataFrame with t_src_ms} as a campaign-runner telemetry.csv: frames in drone-time order,
    `batch` frames per WiFi batch sharing one received_ns (30 ms after its last frame), each frame's `slotN.<var>`
    rows then its link stats (meta) and the telemetry_adapter aliases. grouped puts a batch's frames of one lane
    together (several frames of a lane in one run)."""
    lanes = {w: (list(df.columns.drop("t_src_ms")), df["t_src_ms"].to_numpy(float),
                 df.drop(columns="t_src_ms").to_numpy(float)) for w, df in lanes.items()}
    frames = sorted((int(t), w, i) for w, (_, ts, _) in lanes.items() for i, t in enumerate(ts))
    seq = dict.fromkeys(lanes, 0)
    lines = ["received_ns,slot,key,value"]
    for b in range(0, len(frames), batch):
        chunk = frames[b:b + batch]
        ns = T0_NS + int((chunk[-1][0] + 30) * 1e6) + b
        if grouped:
            chunk = sorted(chunk, key=lambda f: f[1])
        for tm, w, i in chunk:
            cols, _, vals = lanes[w]
            seq[w] += 1
            vs = [repr(float(v)) for v in vals[i]]
            row = [(f"slot{w}.{c}", v) for c, v in zip(cols, vs)]
            if meta:
                row += [(f"slot{w}.{k}", v) for k, v in (("t_ms", str(tm)), ("seq", str(seq[w])), ("received", str(seq[w])),
                        ("dropped", "0"), ("crc_errors", "0"), ("bytes_received", str(64 * seq[w])), ("loss_pct", "0.0"))]
            if aliases:
                row += list(zip(cols, vs)) + [("bytes_received", str(64 * seq[w]))]
                if "DroneStatus.ARM_Status" in cols:
                    row.append(("status.arm", vs[cols.index("DroneStatus.ARM_Status")]))
            lines += [f"{ns},{w},{k},{v}" for k, v in row]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def assert_same(a, b, path="", rtol=1e-9, atol=1e-12):
    """Nested result dicts equal (the flight's name / stem aside)."""
    if isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            if k not in ("name", "stem"):
                assert_same(a[k], b[k], f"{path}.{k}", rtol, atol)
    elif isinstance(a, (str, bool, np.bool_)) or a is None:
        assert a == b, path
    else:
        np.testing.assert_allclose(np.asarray(a), np.asarray(b), rtol=rtol, atol=atol, err_msg=path)


def r4_lanes():
    """R4 slots 0-3 at their own rates and phases, 20 s."""
    rng = np.random.default_rng(3)
    spec = {0: (50, 10003, ("DroneStatus.ARM_Status", "Battery.voltage")), 1: (100, 10001, ("imu_data.yaw", *vt.NN4_OF)),
            2: (25, 10007, ("ano_of.earth_x", "ano_of.earth_y", vt.Z_M, "s_ekf_of.x[0]", "s_ekf_of.x[1]")),
            3: (10, 10011, ("Ctrler.locxPID.Des",))}
    lanes = {}
    for k, (hz, t0, cols) in spec.items():
        t = t0 + np.arange(0, 20000, 1000 / hz)
        df = pd.DataFrame({"t_src_ms": t, **{c: rng.normal(size=len(t)) * 100 for c in cols}})
        if k == 0:
            df["xTickCount"] = t
        lanes[k] = on_wire(df)
    return lanes


@pytest.mark.parametrize("grouped", [False, True])
def test_read_slots_reads_the_session_like_the_slot_csvs(tmp_path, grouped):
    lanes = r4_lanes()
    for k, df in lanes.items():
        df.to_csv(tmp_path / f"r4.slot{k}.csv", index=False)
    write_session(tmp_path / "ses" / "telemetry.csv", {(k + 1) % 4: df for k, df in lanes.items()}, grouped=grouped)
    a = vt.read_slots(str(tmp_path / "r4"))
    for b in (vt.read_slots(str(tmp_path / "ses")), vt.read_slots(str(tmp_path / "ses" / "telemetry.csv"))):
        assert sorted(a) == sorted(b) == [0, 1, 2, 3]
        for k in a:
            np.testing.assert_array_equal(b[k]["t"], a[k]["t"])
            for c in a[k]:
                np.testing.assert_allclose(b[k][c], a[k][c], rtol=1e-12, err_msg=f"slot{k} {c}")
    want = log_corpus.hold({"y": (a[1]["t"], a[1]["imu_data.yaw"])}, "y", a[2]["t"])
    np.testing.assert_allclose(b[2]["imu_data.yaw"], want, rtol=1e-12)          # another lane: held onto slot 2
    assert "imu_data.yaw" in b[2] and "imu_data.yaw" not in a[2]
    assert "nope" not in b[2] and b[2].get("nope") is None
    with pytest.raises(KeyError):
        b[2]["nope"]
    assert sorted(vt.read_slots(str(tmp_path / "ses"), (1, 2))) == [1, 2]


def test_session_without_frame_times_falls_back_to_the_tick_clock(tmp_path):
    lanes = r4_lanes()
    write_session(tmp_path / "telemetry.csv", {(k + 1) % 4: df for k, df in lanes.items()}, meta=False)
    t = vt.read_slots(str(tmp_path), (1,))[1]["t"]
    t1 = lanes[1]["t_src_ms"].to_numpy(float) / 1e3
    assert 1 < len(t) < len(t1)                                                # one time per WiFi batch
    i = np.clip(np.searchsorted(t1, t), 1, len(t1) - 1)
    assert np.minimum(np.abs(t1[i] - t), np.abs(t1[i - 1] - t)).max() < 0.05
    assert abs(t[0] - t1[0]) < 0.05 and abs(t[-1] - t1[-1]) < 0.05


def test_timebase_absolute_keeps_the_board_clock():
    raw = {"xTickCount": (np.array([0.0, 1.0, 2.0]), np.array([5000.0, 6000.0, 7000.0]))}
    assert log_corpus._timebase(raw)(1.5) == pytest.approx(1.5)
    assert log_corpus._timebase(raw, absolute=True)(1.5) == pytest.approx(6.5)


def test_campaign_session_rebuilds_earth_from_the_position_loop_feedback(tmp_path):
    rng = np.random.default_rng(5)
    t = 5000.0 + np.arange(0, 4000, 20)
    fast = on_wire(pd.DataFrame({"t_src_ms": t, "DroneStatus.ARM_Status": 1.0, vt.Z_M: rng.normal(size=len(t)),
                                 vt.X_CM: 50 * rng.normal(size=len(t)), vt.Y_CM: 50 * rng.normal(size=len(t))}))
    slow = on_wire(pd.DataFrame({"t_src_ms": t[::2] + 7, "imu_data.yaw": rng.normal(size=len(t[::2]))}))
    write_session(tmp_path / "telemetry.csv", {1: fast, 2: slow})
    S = vt.read_slots(str(tmp_path / "telemetry.csv"))
    assert sorted(S) == [0, 1, 2]
    np.testing.assert_array_equal(S[2]["t"], t / 1e3)
    np.testing.assert_allclose(S[2]["ano_of.earth_x"], -fast[vt.Y_CM], rtol=1e-12)
    np.testing.assert_allclose(S[2]["ano_of.earth_y"], fast[vt.X_CM], rtol=1e-12)


def fixes_flight(tmp_path):
    """test_video_truth_plumb's tracker fixes (60 lost) on the fusion slot2 counters at 25 Hz, 15 of the arrivals
    old (n_old, not n_ok); the counters start at 9838 (carried from earlier flights)."""
    rng = np.random.default_rng(0)
    sent = 400000.0 + np.cumsum(rng.uniform(0.040, 0.054, 3000))
    c_true, lat = 399900.0, 0.004
    got = np.r_[:20, np.sort(20 + rng.choice(len(sent) - 20, len(sent) - 80, replace=False))]
    arr = sent[got] - c_true + lat + rng.uniform(0, 0.003, len(got))
    old = np.zeros(len(got), bool)
    old[rng.choice(np.arange(200, len(got)), 15, replace=False)] = True
    fixes = tmp_path / "fixes.csv"
    fixes.write_text("t_send,t_cap,pad_x_m,pad_y_m,earth_x_cm,earth_y_cm,frame_hex\n"
                     + "".join(f"{s:.4f},{s - 0.03:.4f},0.1,0.2,0,0,ccdd\n" for s in sent))
    tm = np.arange(int(arr[0] * 1e3) - 2000, int(arr[-1] * 1e3) + 100, 40).astype(float)
    j = np.searchsorted(arr, tm / 1e3, "right")
    g = "g_ext_pos."
    rows = pd.DataFrame({"t_src_ms": tm, g + "n_ok": 9838.0 + np.r_[0, np.cumsum(~old)][j], g + "n_rej": 5.0,
                         g + "n_old": np.r_[0, np.cumsum(old)][j],
                         g + "age_ms": np.where(j > 0, np.round((tm / 1e3 - arr[np.maximum(j - 1, 0)]) * 1e3), 0.0)})
    return fixes, rows, c_true - lat - 0.0015, len(got)


def test_fixes_clock_reads_the_session_like_the_slot_csv(tmp_path):
    fixes, rows, c_want, n_got = fixes_flight(tmp_path)
    no_old = rows.drop(columns="g_ext_pos.n_old")                              # the campaign capture's columns
    no_old.to_csv(tmp_path / "fx.slot2.csv", index=False)
    rows.to_csv(tmp_path / "fo.slot2.csv", index=False)
    tm = rows["t_src_ms"].to_numpy()
    yaw = pd.DataFrame({"t_src_ms": np.arange(tm[0] + 3, tm[-1], 20.0), "imu_data.yaw": 1.0})
    write_session(tmp_path / "ses" / "telemetry.csv", {3: no_old, 0: yaw})
    fx, fo = (vt.fixes_clock(str(tmp_path / s), fixes) for s in ("fx", "fo"))
    assert_same(vt.fixes_clock(str(tmp_path / "ses"), fixes), fx, rtol=1e-12, atol=1e-6)
    for fc in (fx, fo):                                                        # an old fix without n_old = a lost one
        assert abs(fc["c"] - c_want) < 0.002 and fc["lost_monotone_frac"] > 0.99
    assert fx["n_recv"] == n_got - 15 and fo["n_recv"] == n_got
    write_session(tmp_path / "yaw" / "telemetry.csv", {0: yaw})
    with pytest.raises(SystemExit):
        vt.fixes_clock(str(tmp_path / "yaw"), fixes)


def session_copy(root, name, lanes):
    """The truth4 flight `name` again as a campaign session: its slot columns in a telemetry.csv on the given lanes,
    and a video_truth dir <name>_session whose truth4.json stem is that session."""
    stem = f"logs/campaigns/{name}_20261019-151725"
    write_session(root / stem / "telemetry.csv", lanes)
    src, vd = root / "logs" / "video_truth" / name, root / "logs" / "video_truth" / f"{name}_session"
    vd.mkdir()
    (vd / "truth4.csv").write_bytes((src / "truth4.csv").read_bytes())
    (vd / "truth4.json").write_text(json.dumps(json.loads((src / "truth4.json").read_text()) | {"stem": stem}))


@pytest.fixture
def nn_flights(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nn.write_flight(tmp_path, "plain", lambda t: 0.01 * (t - nn.T0) * (1 + 0.5j))
    nn.write_flight(tmp_path, "fused", lambda t: 0.002 * np.sin(t - nn.T0), ekf_noise=0.05, ext_on=2, seed=1)
    syn = tmp_path / "logs" / "syn"
    for p in syn.glob("*.csv"):
        on_wire(pd.read_csv(p)).to_csv(p, index=False)
    session_copy(tmp_path, "plain", {0: pd.read_csv(syn / "plain.slot1.csv"), 1: pd.read_csv(syn / "plain.slot2.csv")})
    f1 = pd.read_csv(syn / "fused.slot1.csv")
    session_copy(tmp_path, "fused", {0: pd.read_csv(syn / "fused.slot0.csv"), 1: f1.drop(columns="g_ext_pos_on"),
                                     2: f1[["t_src_ms", "g_ext_pos_on"]].iloc[::4]})
    return tmp_path


@pytest.mark.parametrize("name", ["plain", "fused"])
def test_nn4_data_reads_the_session_like_the_slot_csvs(nn_flights, name):
    for kw in ({}, {"target": "earth"}):
        a, b = vt.nn4_data(name, **kw), vt.nn4_data(f"{name}_session", **kw)
        assert_same(b, a)
    assert b["ext_pos_on"] == (2 if name == "fused" else 0)


def smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def path(t):
    """Drone track (m, floor frame) on the drone clock: on the pad until 15 s, back on it from 59 s."""
    tau = t - 15.0
    return smooth(tau) * smooth(59.0 - t) * (0.4 * np.sin(0.5 * tau) + 0.3j * (1 - np.cos(0.35 * tau)))


def height(t):
    return 0.5 * smooth(t - 15.0) * smooth(60.0 - t)


@pytest.fixture
def video(tmp_path, monkeypatch):
    """A floor-plane camera (pixel = 1 mm, the cast grows 10 %/m of ring height) whose clock is the drone's - 10 s."""
    monkeypatch.setattr(vt, "floor_from_marker", lambda scan, cam, *a, **k: (np.eye(3), np.zeros(3), 0.4))
    monkeypatch.setattr(vt, "pixel_to_plane", lambda uv, K, dist, R, t, z: np.c_[
        np.asarray(uv) / 1000 * (1 + 0.1 * np.asarray(z, float).reshape(-1, 1)), np.zeros(len(uv))])
    monkeypatch.setattr(vt, "ring_height", lambda *a, **k: 0.05)
    vd = tmp_path / "vid"
    vd.mkdir()
    np.savez(vd / "aruco.npz", c0=np.zeros((1, 4, 2)))
    t_v = np.arange(0, 60, 1 / 30)
    p = path(t_v + 10.0)
    np.savez(vd / "coral.npz", t=t_v, uv=np.c_[p.real, p.imag] * 1000)
    return str(vd)


@pytest.mark.parametrize("campaign", [False, True])
def test_r4_truth_reads_the_session_like_the_slot_csvs(tmp_path, video, campaign):
    fast, slow = 10003.0 + np.arange(0, 60000, 20), 10007.0 + np.arange(0, 60000, 40)
    arm = lambda t: ((t >= 12) & (t < 66)).astype(float)
    if campaign:
        # campaign capture: earth only as the position-loop feedback, height and arm on the same lane, no EKF
        s = fast / 1e3
        e = 100 * path(s) * np.exp(1j * np.radians(30))
        lane = on_wire(pd.DataFrame({"t_src_ms": fast, "DroneStatus.ARM_Status": arm(s), vt.Z_M: height(s),
                                     vt.X_CM: e.imag, vt.Y_CM: -e.real, "xTickCount": fast}))
        yaw = on_wire(pd.DataFrame({"t_src_ms": slow, "imu_data.yaw": 10 * np.sin(slow / 1e4)}))
        lane[["t_src_ms", "DroneStatus.ARM_Status"]].to_csv(tmp_path / "r4.slot0.csv", index=False)
        pd.DataFrame({"t_src_ms": lane["t_src_ms"], "ano_of.earth_x": -lane[vt.Y_CM], "ano_of.earth_y": lane[vt.X_CM],
                      vt.Z_M: lane[vt.Z_M]}).to_csv(tmp_path / "r4.slot2.csv", index=False)
        write_session(tmp_path / "ses" / "telemetry.csv", {2: lane, 0: yaw})
    else:
        s = slow / 1e3
        e, k = 100 * path(s) * np.exp(1j * np.radians(30)), path(s) * np.exp(-1j * np.radians(40))
        s0 = on_wire(pd.DataFrame({"t_src_ms": fast, "DroneStatus.ARM_Status": arm(fast / 1e3)}))
        s2 = on_wire(pd.DataFrame({"t_src_ms": slow, "ano_of.earth_x": e.real, "ano_of.earth_y": e.imag, vt.Z_M: height(s),
                                   "s_ekf_of.x[0]": k.real, "s_ekf_of.x[1]": k.imag}))
        s0.to_csv(tmp_path / "r4.slot0.csv", index=False)
        s2.to_csv(tmp_path / "r4.slot2.csv", index=False)
        write_session(tmp_path / "ses" / "telemetry.csv", {3: s0, 1: s2})
    a = vt.r4_truth(str(tmp_path / "r4"), video, CAM)
    b = vt.r4_truth(str(tmp_path / "ses"), video, CAM)
    assert abs(a["summary"]["offset_s"] - 10.0) < 0.1
    assert_same(b, a)
    assert set(a["summary"]["est"]) == ({"earth"} if campaign else {"earth", "ekf"})
