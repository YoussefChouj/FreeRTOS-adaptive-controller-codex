import cv2
import numpy as np

from ground_station.analysis.video_truth import fixes_clock, plumb_holds, ray_at_vertical, read_slots, ring_height


def _cam(C=(0.0, -2.0, 1.5)):
    C = np.array(C)
    f = -C / np.linalg.norm(C)
    x = np.cross(f, [0.0, 0.0, 1.0]); x /= np.linalg.norm(x)
    R = np.array([x, np.cross(f, x), f])
    K = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]])
    return K, np.zeros(5), R, -R @ C


def test_ray_meets_the_marker_vertical_at_the_true_height():
    K, dist, R, t = _cam()
    P = np.array([[0.3, 0.2, 0.8]])
    uv = cv2.projectPoints(P, cv2.Rodrigues(R)[0], t, K, dist)[0].reshape(2)
    z, miss = ray_at_vertical(uv, K, dist, R, t, (0.3, 0.2))
    assert abs(z - 0.8) < 1e-6 and miss < 1e-6
    z2, miss2 = ray_at_vertical(uv, K, dist, R, t, (0.4, 0.2))           # 10 cm plumb error across the ray
    assert miss2 > 0.05 and abs(z2 - 0.8) < 0.05
    uv0 = cv2.projectPoints(np.array([[0.0, 0.0, 0.07]]), cv2.Rodrigues(R)[0], t, K, dist)[0].reshape(2)
    assert abs(ring_height(uv0, K, dist, R, t) - 0.07) < 1e-6


def test_holds_are_the_still_runs_above_the_floor():
    t = np.arange(0, 60, 0.01)
    z = np.interp(t, [0, 10, 11, 20, 21, 30, 31, 33, 34, 60], [0, 0, 0.5, 0.5, 1.0, 1.0, 0.3, 0.3, 0, 0])
    z = z + np.random.default_rng(0).normal(0, 0.002, len(t))
    h = plumb_holds(t, z)
    assert len(h) == 2                                                    # the 2 s hold at 0.3 m is too short
    assert 11 < h[0][0] < 12.5 and 18.5 < h[0][1] < 20 and 21 < h[1][0] < 22.5


def test_read_slots_maps_a_fusion_log_onto_the_r4_slot_numbers(tmp_path):
    stem = tmp_path / "f"
    (tmp_path / "f.slot0.csv").write_text("t_src_ms,t_host_s,seq,Acc_X_Real\n1000,0,0,1.5\n")
    (tmp_path / "f.slot1.csv").write_text("t_src_ms,t_host_s,seq,g_ext_pos_on,ano_of.earth_x\n1000,0,0,0,7\n")
    (tmp_path / "f.slot2.csv").write_text("t_src_ms,t_host_s,seq,mrac_state.pitch.Theta[0]\n1000,0,0,0\n")
    S = read_slots(str(stem))
    assert sorted(S) == [1, 2] and S[1]["Acc_X_Real"][0] == 1.5 and S[2]["ano_of.earth_x"][0] == 7
    (tmp_path / "f.slot1.csv").write_text("t_src_ms,t_host_s,seq,ano_of.earth_x\n1000,0,0,7\n")   # R4 layout
    S = read_slots(str(stem))
    assert sorted(S) == [0, 1, 2] and "Acc_X_Real" in S[0]


def test_fixes_clock_recovers_the_offset_through_lost_packets(tmp_path):
    rng = np.random.default_rng(0)
    sent = 400000.0 + np.cumsum(rng.uniform(0.040, 0.054, 3000))       # tracker monotonic s, ~47 ms per fix
    c_true, lat = 400100.0, 0.004                                       # drone s = monotonic s - c_true; WiFi 4 ms
    got = np.r_[:20, np.sort(20 + rng.choice(len(sent) - 20, len(sent) - 80, replace=False))]  # 2 % lost after the 20th
    arr = sent[got] - c_true + lat + rng.uniform(0, 0.003, len(got))
    with open(tmp_path / "fixes.csv", "w") as f:
        f.write("t_send,t_cap,pad_x_m,pad_y_m,earth_x_cm,earth_y_cm,frame_hex\n")
        f.writelines(f"{s:.4f},{s - 0.03:.4f},0.1,0.2,0,0,ccdd\n" for s in sent)
    t = np.arange(arr[0], arr[-1], 0.04)                                # 25 Hz slot2 rows
    k = np.searchsorted(arr, t, side="right")
    with open(tmp_path / "f.slot2.csv", "w") as f:
        f.write("t_src_ms,g_ext_pos.n_ok,g_ext_pos.n_rej,g_ext_pos.n_old,g_ext_pos.age_ms\n")
        f.writelines(f"{ti * 1e3:.1f},{ki - 5},5,0,{(ti - arr[ki - 1]) * 1e3:.1f}\n" for ti, ki in zip(t, k) if ki > 5)
    fc = fixes_clock(str(tmp_path / "f"), tmp_path / "fixes.csv")
    assert abs(fc["c"] - (c_true - lat - 0.0015)) < 0.002              # c absorbs the mean latency
    assert fc["n_sent"] == 3000 and 2935 <= fc["n_recv"] <= 2940 and fc["lost_monotone_frac"] > 0.99 and fc["resid_p90_ms"] < 3.0
    assert abs(fc["t"][0] - (sent[0] - 0.03 - fc["c"])) < 1e-3
