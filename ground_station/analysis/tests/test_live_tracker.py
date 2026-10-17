"""live_tracker: earth alignment fit, fix scheduler (median, spread, period), frame packing."""
import shutil
import struct

import numpy as np
import pytest

from ground_station.analysis.live_tracker import EarthAlign, FixScheduler
from ground_station.comm.ext_pos_encoder import ext_pos_decode


def _path(n=200):
    s = np.linspace(0, 2 * np.pi, n)
    return np.c_[0.6 * np.cos(s) + 0.2, 0.4 * np.sin(2 * s) - 0.1]


@pytest.mark.parametrize("mirror", [False, True])
def test_fit_recovers_rotation_offset_and_handedness(mirror):
    cam = _path()
    rot = np.exp(1j * np.radians(31.0))
    zc = cam[:, 0] + 1j * cam[:, 1]
    ze = 100 * rot * (zc.conj() if mirror else zc) + (12 - 7j)
    earth = np.c_[ze.real, ze.imag] + np.random.default_rng(0).normal(0, 0.5, (len(ze), 2))
    al = EarthAlign.fit(cam, earth)
    assert al.mirror == mirror
    assert abs(np.degrees(np.angle(al.a)) - 31.0) < 0.5 and abs(al.scale - 1) < 0.01 and al.rms_cm < 1.0
    for k in (0, 57, 140):
        assert np.allclose(al.apply(cam[k]), [ze[k].real, ze[k].imag], atol=0.5)


def test_fit_refuses_a_hover():
    with pytest.raises(ValueError):
        EarthAlign.fit(np.zeros((20, 2)), np.zeros((20, 2)))


def test_scheduler_median_period_and_spread():
    s = FixScheduler(period=1.0, n_med=5, max_spread_m=0.05)
    assert s.due(0.0) is None                                  # arms the clock
    for k in range(30):
        s.push(0.03 * k, [0.1, 0.2 + {25: 0.5, 27: -0.5, 29: 0.9}.get(k, 0.0)])   # 3 outliers in the last 5 -> skip
    assert s.due(1.0) is None and s.n_skip == 1
    for k in range(30):
        s.push(1.0 + 0.03 * k, [0.1 + 0.001 * k, 0.2 + (0.5 if k == 29 else 0.0)])   # 1 outlier: dropped
    t, xy = s.due(2.0)
    assert np.allclose(xy, [0.1265, 0.2]) and abs(t - (1.0 + 0.03 * 26.5)) < 1e-9
    assert s.due(2.5) is None                                  # not due before the next period
    assert s.due(3.0) is None and s.n_skip == 2                # nothing new in the last second


def test_scheduler_catches_up_after_a_stall():
    s = FixScheduler(period=1.0, n_med=1)
    s.due(0.0)
    s.push(4.9, [0, 0])
    assert s.due(5.2) is not None and s.next_t == 6.0


def test_fix_frame_carries_earth_cm():
    from ground_station.comm.ext_pos_encoder import ext_pos_fix_frame
    al = EarthAlign(a=1j, cam0=0.1 + 0j, earth0=5 + 0j)        # pad x -> earth y
    e = al.apply([0.3, 0.0])
    assert np.allclose(e, [5.0, 20.0])
    f = ext_pos_fix_frame(*e, 0.37)
    assert f[3] == 37 and ext_pos_decode(struct.unpack("<f", f[4:8])[0]) == (5.0, 20.0)


def _ring_frame(c, scenery=True):
    cv2 = pytest.importorskip("cv2")
    f = np.full((480, 640, 3), 90, np.uint8)
    if scenery:
        cv2.rectangle(f, (520, 60), (600, 140), (0, 0, 255), -1)            # fixed red object, lit from frame 0
    if c is not None:
        cv2.circle(f, (int(round(c[0])), int(round(c[1]))), 30, (0, 0, 255), 6)
    return f


def test_ring_tracker_follows_the_ring_and_ignores_scenery():
    from ground_station.analysis.live_tracker import RingTracker
    tr = RingTracker(seed_px=(200, 300))
    path = [(200 + 4 * k, 300 - 2 * k) for k in range(60)]                  # passes within ~150 px of the scenery
    errs = [np.linalg.norm(tr.step(_ring_frame(c)) - c) for c in path]
    assert max(errs) < 4.0


def test_ring_tracker_coasts_then_reacquires():
    from ground_station.analysis.live_tracker import RingTracker
    tr = RingTracker(seed_px=(200, 300))
    for k in range(10):
        tr.step(_ring_frame((200 + 3 * k, 300)))
    assert all(tr.step(_ring_frame(None)) is None for _ in range(5)) and tr.lost == 5
    uv = tr.step(_ring_frame((260, 300)))
    assert uv is not None and np.linalg.norm(uv - (260, 300)) < 4.0 and tr.lost == 0


def test_tel_height_shifts_time_and_adds_ring_offset():
    from ground_station.analysis.live_tracker import tel_height
    k = "slot0.Ctrler.Z_posPID.FB"
    h = tel_height({k + "|t": np.array([0.0, 10.0]), k: np.array([0.0, 1.0])}, tel_offset_s=15.0, ring_offset_m=0.05)
    assert abs(h(20.0) - 0.55) < 1e-12 and abs(h(0.0) - 0.05) < 1e-12      # video 20 s = tel 5 s; clamps before tel 0


_CORAL = (40, 90, 230)                                         # BGR: OpenCV hue ~8, sat ~210, val 230 -> coral_mask


def _coral_frame(c, blob=None):
    import cv2
    f = np.zeros((480, 640, 3), np.uint8)
    cv2.circle(f, (int(c[0]), int(c[1])), 15, _CORAL, -1)
    if blob is not None:
        f[blob[1] - 10:blob[1] + 10, blob[0] - 10:blob[0] + 10] = _CORAL
    return f


def test_coral_tracker_follows_masks_static_and_grows_on_a_miss():
    from ground_station.analysis.live_tracker import CoralTracker
    static = np.zeros((480, 640), bool)
    static[170:190, 290:310] = True                            # a coral object 80 px from the drone, in the ROI
    tr = CoralTracker([300, 260], static=static)
    for k in range(5):
        c = (300 + 10 * k, 260)
        uv = tr.step(_coral_frame(c, blob=(300, 180)))
        assert uv is not None and np.hypot(*(uv - c)) <= 1.0
    assert tr.step(np.zeros((480, 640, 3), np.uint8)) is None and tr.r == 210.0
    tr.recentre([100, 100])
    assert tr.r == 140.0 and np.allclose(tr.c, [100, 100])


def test_jump_gate_reseeds_only_after_the_track_is_lost_for_lost_s():
    """2026-10-15 lab: a static wrong blob won 10 frames in a row and the track ping-ponged every few seconds."""
    from ground_station.analysis.live_tracker import JumpGate
    g = JumpGate(v_max=1.0, slack_m=0.05, n_reseed=3, cluster_m=0.05, lost_s=1.0)
    assert g.check(0.0, [0, 0])
    assert not any(g.check(t, [1.0, 0]) for t in (0.1, 0.2, 0.3, 0.4))   # agreeing rejects, but lost only 0.4 s
    assert g.check(0.45, [0.02, 0]) and g.reseeds == 0                     # the real track is back
    assert not any(g.check(0.45 + 0.1 * k, [1.0, 0]) for k in range(1, 10))
    assert g.check(1.5, [1.0, 0]) and g.reseeds == 1                       # lost 1.05 s: the drone really moved


def test_jump_gate_rejects_jumps_caps_dt_and_reseeds_on_a_cluster():
    from ground_station.analysis.live_tracker import JumpGate
    g = JumpGate(v_max=1.0, slack_m=0.05, dt_max=0.5, n_reseed=3, cluster_m=0.05, lost_s=0.0)
    assert g.check(0.0, [0, 0]) and g.check(0.1, [0.1, 0])     # 0.1 m in 0.1 s at 1 m/s + slack
    assert not g.check(0.2, [1.0, 0]) and not g.check(0.3, [1.0, 0.01])
    assert g.check(0.4, [1.01, 0]) and g.reseeds == 1 and g.n_rej == 3   # 3 agreeing rejects: drone moved
    assert g.check(0.5, [1.02, 0])
    assert not g.check(10.0, [3.0, 0])                         # 9.5 s gap still allows only 0.5 s of travel


def test_scheduler_window_outlasts_the_period():
    s = FixScheduler(period=0.1, n_med=3, window_s=0.2)
    s.due(0.0)
    for t in (0.04, 0.11, 0.18):                               # one frame per 0.1 s period would be < n_med
        s.push(t, [0.0, 0.0])
    assert s.due(0.2) is not None


def test_scheduler_window_widens_at_low_frame_rate():
    s = FixScheduler(period=0.05, n_med=2, window_s=0.1)          # the 2026-10-15 args: 0 fixes at ~6 fps
    s.due(0.0)
    sent = 0
    for k in range(1, 40):
        t = 0.16 * k
        s.push(t, [0.0, 0.0])
        sent += s.due(t) is not None
    assert 0.1 < s.window_s() <= 0.5
    assert sent >= 30


def test_scheduler_window_stays_tight_at_high_frame_rate():
    s = FixScheduler(period=0.05, n_med=2, window_s=0.1)
    s.due(0.0)
    for k in range(1, 60):
        s.push(k / 30.0, [0.0, 0.0])
        s.due(k / 30.0)
    assert s.window_s() == 0.1


def test_run_source_sends_hello_then_fixes_when_latency_exceeds_period(tmp_path):
    import cv2

    from ground_station.analysis.live_tracker import CoralTracker, run_source
    p = str(tmp_path / "v.avi")
    w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (640, 480))
    for k in range(45):
        w.write(_coral_frame((300 + k, 260)))
    w.release()
    cam = {"K": [[500.0, 0, 320], [0, 500.0, 240], [0, 0, 1]], "dist": [0.0] * 5,
           "R": [[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]], "t": [0.0, 0.0, 2.0]}
    sent, fixes = [], []
    st = run_source(p, cam, EarthAlign(), 0.05, cam_latency_s=0.15, send=sent.append, ring=CoralTracker([300, 260]),
                    sched=FixScheduler(0.1, 3, window_s=0.2), mode=2, delay_ms=300.0, fix_log=fixes)
    assert sent[0][2] == 0x2C and sent[0][3] == 0xFE and sent[1][3] == 0xFF   # delay, then mode, once for a file
    assert st["fixes"] >= 10 and len(fixes) == st["fixes"] and len(sent) == 2 + st["fixes"]
    for t_read, t_fix, b in fixes:
        assert b[3] == round((t_read - t_fix) * 100) and b[3] >= 15            # age includes the camera latency
        assert ext_pos_decode(struct.unpack("<f", b[4:8])[0]) is not None


def test_udp_sender_sends_one_datagram_per_frame():
    import socket

    from ground_station.analysis.live_tracker import udp_sender
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    r.bind(("127.0.0.1", 0))
    r.settimeout(2.0)
    udp_sender(f"udp:{r.getsockname()[1]}")(b"\xcc\xdd\x2c")
    assert r.recv(64) == b"\xcc\xdd\x2c"
    with pytest.raises(ValueError):
        udp_sender("tcp:1")


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc for the API/ekf_of.c host build")
def test_firmware_path_matches_the_python_mirror():
    """fw_fusion (the real API/ekf_of.c camera-fix path) and vt.ext_pos_replay agree on a drifting estimate fed
    20 Hz fixes 0.15 s late: same fix counts, fused paths within 1 cm (float32, 5 ms ticks, 10 ms drain)."""
    from ground_station.analysis import video_truth as vt
    from ground_station.analysis.live_tracker import fw_fusion
    rng = np.random.default_rng(1)
    t = np.arange(0.0, 40.0, 0.01) + 3.0
    truth = np.c_[0.3 * np.sin(0.4 * t), 0.2 * np.cos(0.3 * t)]
    est = truth + np.c_[0.02 * (t - 3), -0.015 * (t - 3)] + np.c_[0.01 * np.sin(5 * t), np.zeros_like(t)]
    t_read = np.arange(3.5, 42.5, 0.05)
    idx = np.full(len(t_read), 3)
    t_fix = t_read - idx * 0.01
    xy = np.c_[np.interp(t_fix, t, truth[:, 0]), np.interp(t_fix, t, truth[:, 1])] + rng.normal(0, 0.005, (len(t_fix), 2))
    xy[200] += 0.8                                                        # one wild fix: gated by both
    arr, L = t_read + 0.15, 0.15
    py, n_py = vt.ext_pos_replay(t, est, arr - idx * 0.01 - L, xy, 0.0, vt.EXT_POS, arr_t=arr)
    fw, n_fw = fw_fusion(t, est, arr, idx, xy, L * 1000)
    assert n_fw == n_py and n_fw["gated"] >= 1
    k = t > 5.0
    assert np.abs(fw - py)[k].max() < 0.01
    assert np.abs(fw - truth)[t > 10].max() < 0.05


def test_tel_height_tails_a_growing_csv_with_partial_lines_and_goes_stale(tmp_path):
    from ground_station.analysis.live_tracker import TelHeight
    p, now = tmp_path / "s.slot1.csv", [0.0]
    tel = TelHeight(p, ring_h_m=0.05, stale_s=1.0, clock=lambda: now[0])
    assert np.isnan(tel(0.0)) and not tel.ground                       # no file yet, no fallback: no plane
    with p.open("w", newline="", encoding="utf-8", buffering=1) as f:
        f.write("t,Ctrler.Z_posPID.FB\r\n1,0.01\r\n2,0.4")             # last row still being written
        f.flush()
        assert tel(0.0) == pytest.approx(0.06) and tel.ground
        f.write("0\r\n")
        f.flush()
        assert tel(0.0) == pytest.approx(0.45) and not tel.ground
    now[0] = 2.0
    assert np.isnan(tel(0.0))
    tel.fallback = 0.9
    assert tel(0.0) == 0.9


def test_tel_height_ground_is_relative_to_the_pad_reading(tmp_path):
    """r7_f2: z read 0.050 m on the pad, so an absolute z < 0.03 never held and PadOrigin sent 0 fixes."""
    from ground_station.analysis.live_tracker import TelHeight
    p = tmp_path / "s.slot1.csv"
    p.write_text("t,Ctrler.Z_posPID.FB\n1,0.050\n2,0.052\n", encoding="utf-8")
    tel = TelHeight(p, clock=lambda: 0.0)
    tel(0.0)
    assert tel.ground
    with p.open("a", encoding="utf-8") as f:
        f.write("3,0.30\n")
    tel(0.0)
    assert not tel.ground


def test_pad_origin_follows_on_ground_then_freezes_after_take_off():
    from ground_station.analysis.live_tracker import PadOrigin
    pad = PadOrigin(n=3)
    for x in (0.10, 0.12, 0.11):
        pad.apply([x, -0.2], True)
    assert pad.apply([0.61, 0.3], False) == pytest.approx([0.5, 0.5]) and pad.frozen
    assert pad.apply([0.0, 0.0], True) == pytest.approx([-0.11, 0.2])   # landed elsewhere: origin kept
    auto = PadOrigin(n=2)
    assert auto.apply([1.0, 1.0], None) == pytest.approx([0, 0])
    auto.apply([1.0, 1.0], None)
    assert auto.apply([2.0, 1.0], None) == pytest.approx([1.0, 0.0])


def test_run_source_writes_fixes_as_it_goes_and_ends_on_the_stop_file(tmp_path):
    import cv2

    from ground_station.analysis.live_tracker import CoralTracker, run_source
    p = str(tmp_path / "v.avi")
    w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (640, 480))
    for k in range(45):
        w.write(_coral_frame((300 + k, 260)))
    w.release()
    cam = {"K": [[500.0, 0, 320], [0, 500.0, 240], [0, 0, 1]], "dist": [0.0] * 5,
           "R": [[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]], "t": [0.0, 0.0, 2.0]}
    out, stop = tmp_path / "fixes.csv", tmp_path / "stop"
    st = run_source(p, cam, EarthAlign(), 0.05, out_csv=str(out), ring=CoralTracker([300, 260]),
                    sched=FixScheduler(0.1, 3, window_s=0.2), stop_file=str(stop))
    assert st["fixes"] >= 10 and len(out.read_text().splitlines()) == 1 + st["fixes"]
    stop.touch()
    st = run_source(p, cam, EarthAlign(), 0.05, out_csv=str(out), ring=CoralTracker([300, 260]), stop_file=str(stop))
    assert st["frames"] == 0 and out.read_text().splitlines()[0].startswith("t_send")


def test_stream_log_stop_file_sets_once_the_file_appears(tmp_path):
    from ground_station.livewatch.stream_log import StopFile
    ev = StopFile(tmp_path / "stop")
    assert not ev.is_set()
    (tmp_path / "stop").touch()
    ev._t = 0.0                                   # skip the 0.5 s disk throttle
    assert ev.is_set() and ev.is_set()


def test_shadow_pairs_rebuild_the_sent_fix_from_the_firmware_innovation(tmp_path):
    import pandas as pd

    from ground_station.analysis.live_tracker import shadow_pairs
    t = np.arange(0, 20000, 20.0)                                    # slot1 50 Hz, device ms
    air = (t > 2000) & (t < 18000)
    ex, ey = 100 * np.sin(t / 3000), 80 * np.cos(t / 2000)           # EKF earth, cm
    pd.DataFrame({"t_src_ms": t, "ano_of.earth_x": ex, "ano_of.earth_y": ey,
                  "Ctrler.Z_posPID.FB": np.where(air, 0.8, 0.0)}).to_csv(tmp_path / "s.slot1.csv", index=False)
    t2 = t[::2]
    a = np.exp(1j * np.radians(-100))                                # sent fix = pad at yaw 0 = earth rotated back
    z = a * (ex[::2] + 1j * ey[::2])
    pd.DataFrame({"t_src_ms": t2, "g_ext_pos.e_x": z.real - ex[::2], "g_ext_pos.e_y": z.imag - ey[::2],
                  "g_ext_pos.c_x": 0.0, "g_ext_pos.c_y": 0.0,
                  "g_ext_pos.n_ok": np.arange(len(t2)), "g_ext_pos.n_rej": 0}).to_csv(tmp_path / "s.slot2.csv", index=False)
    ts, cam, earth, run = shadow_pairs(str(tmp_path / "s"))
    assert set(run) == {1} and ts.min() > 2.0 and ts.max() < 18.0
    f = EarthAlign.fit(cam, earth)
    assert np.degrees(np.angle(f.a)) == pytest.approx(100, abs=0.1) and not f.mirror and f.rms_cm < 0.1


def test_fit_frame_rescales_a_resized_window_grab_to_the_start_size():
    from ground_station.analysis import live_tracker as lt
    f = np.zeros((672, 1194, 3), np.uint8)
    assert lt.fit_frame(f, (1194, 672)) is f and lt.fit_frame(f, None) is f
    assert lt.fit_frame(np.zeros((540, 960, 3), np.uint8), (1194, 672)).shape == (672, 1194, 3)


def _l_flight(n=400, dt=0.1):
    """Hover, then a 1 m L (x out, y out) and back: the --align auto manoeuvre."""
    s = np.linspace(0, 1, n)
    x = np.clip(4 * s - 1, 0, 1) - np.clip(4 * s - 3, 0, 1)
    y = np.clip(4 * s - 2, 0, 1) * 0.8 - np.clip(4 * s - 3, 0, 1) * 0.8
    return np.arange(n) * dt, np.c_[x, y]


def test_fit_with_mirror_fixed_keeps_that_handedness():
    _, cam = _l_flight()
    zc = cam[:, 0] + 1j * cam[:, 1]
    ze = 100 * np.exp(1j * np.radians(90.0)) * zc
    al = EarthAlign.fit(cam, np.c_[ze.real, ze.imag], mirror=True)
    assert al.mirror and al.rms_cm > 10.0                              # wrong hand forced: a poor fit, not a flip


def test_auto_align_locks_on_a_90_deg_frame_bumplessly_then_stops():
    from ground_station.analysis.live_tracker import AutoAlign
    t, cam = _l_flight()
    zc = cam[:, 0] + 1j * cam[:, 1]
    ze = 95 * np.exp(1j * np.radians(90.0)) * zc + (40 - 25j)          # r6: ~90 deg, scale ~1, EKF origin elsewhere
    earth = np.c_[ze.real, ze.imag] + np.random.default_rng(1).normal(0, 2.0, (len(t), 2))
    al = EarthAlign()                                                  # --yaw-deg 0 as flown in r6
    aa = AutoAlign(al, target=3)
    locks = [aa.push(t[i], cam[i], earth[i]) for i in range(len(t))]
    assert sum(locks) == 1 and aa.locked and aa.mode == 3
    i = locks.index(True)
    assert abs(np.degrees(np.angle(al.a)) - 90.0) < 5.0 and not al.mirror
    assert np.hypot(*(al.apply(cam[i]) - earth[i])) < 5.0              # fix == EKF at the switch: no step
    assert np.sqrt(((np.array([al.apply(c) for c in cam[i:]]) - earth[i:]) ** 2).sum(1).mean()) < 8.0
    a0 = al.a                                                          # item 6: IMU yaw creeps 10 deg after the lock
    ze2 = 95 * np.exp(1j * np.radians(100.0)) * zc + (40 - 25j)
    for k in range(len(t)):
        aa.push(t[-1] + 0.1 + t[k], cam[k], (ze2[k].real, ze2[k].imag))
    assert abs(aa.drift_deg - 10.0) < 3.0 and al.a == a0 and aa.mode == 3 and "refit +10 deg" in aa.status


def test_auto_align_never_locks_on_a_hover_or_a_wrong_scale():
    from ground_station.analysis.live_tracker import AutoAlign
    t, cam = _l_flight()
    hover = np.random.default_rng(2).normal(0, 0.03, cam.shape)
    aa = AutoAlign(EarthAlign(), target=2)
    assert not any(aa.push(t[i], hover[i], 100 * hover[i]) for i in range(len(t))) and aa.mode == 1
    aa = AutoAlign(EarthAlign(), target=2)
    assert not any(aa.push(t[i], cam[i], 300 * cam[i]) for i in range(len(t))) and "REJ" in aa.status


def test_tel_height_keeps_extra_columns(tmp_path):
    from ground_station.analysis.live_tracker import TelHeight
    p, now = tmp_path / "s.slot1.csv", [0.0]
    p.write_text("t,ano_of.earth_x,Ctrler.Z_posPID.FB,ano_of.earth_y\r\n1,12.5,0.4,-3\r\n", encoding="utf-8")
    tel = TelHeight(p, extra=("ano_of.earth_x", "ano_of.earth_y"), clock=lambda: now[0])
    assert np.allclose(tel.vec(("ano_of.earth_x", "ano_of.earth_y")), [12.5, -3.0])
    now[0] = 5.0
    assert tel.vec(("ano_of.earth_x", "ano_of.earth_y")) is None      # stale


def test_coral_tracker_learns_static_at_start_so_scenery_cannot_hold_the_fix():
    from ground_station.analysis.live_tracker import CoralTracker
    blob, pad, far = (300, 180), (300, 260), (560, 420)        # coral scenery 80 px from the pad, in the ROI
    old = CoralTracker(pad)
    new = CoralTracker(pad, learn_n=4, learn_every=1, keep_r=40)
    for _ in range(4):                                         # drone on the pad: the start frames
        old.step(_coral_frame(pad, blob=blob)), new.step(_coral_frame(pad, blob=blob))
    assert new.static[blob[1], blob[0]] and not new.static[pad[1], pad[0]]
    for _ in range(4):                                         # the drone leaves the ROI in one frame
        uo, un = old.step(_coral_frame(far, blob=blob)), new.step(_coral_frame(far, blob=blob))
    assert np.hypot(*(uo - blob)) < 30                         # no mask: held at the scenery (the r6_f4 lock)
    assert un is not None and np.hypot(*(un - far)) <= 1.0


def test_preview_draws_a_full_size_grab_at_the_set_width():
    from types import SimpleNamespace

    from ground_station.analysis.live_tracker import Preview
    scale = Preview.display_scale
    assert scale(SimpleNamespace(width=716, scale=0.6), 1920) == pytest.approx(716 / 1920)   # 1920 mirror: r7 size
    assert scale(SimpleNamespace(width=716, scale=0.6), 1194) == pytest.approx(0.6, abs=1e-3)
    assert scale(SimpleNamespace(width=716, scale=0.6), 640) == 1.0                         # never upscaled
    assert scale(SimpleNamespace(width=None, scale=0.6), 1920) == 0.6                        # no width: fixed scale


def test_preview_keeps_persistent_traced_path_and_renders_3d_auto_view():
    import ground_station.analysis.video_truth as vt
    from ground_station.analysis.live_tracker import Preview
    p = object.__new__(Preview)
    p.cv2, p.dt, p.scale, p.title, p.t_last, p.ms = vt._cv2(), 0.0, 0.5, "test", -1e9, 0.0
    p.width, p.hz, p.rect, p.traj, p.pass_m, p.trail_s, p.n = None, 30.0, None, None, 0.20, 0.0, 0
    p.trail, p.uvs, p.anchor, p.max_d, p.xy, p.z = [], [], None, 0.0, None, 0.0
    p.z_max, p.view_idx, p.zoom_pip = 0.0, 0, False
    p._az, p._el, p._center = -38.0, 30.0, None
    p.rec, p.recording, p.writer, p.placed, p.full, p.closed = None, False, None, True, False, True
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for k in range(30):
        t = k * 10.0   # spans 290 s (> old 10 s uv and 120 s trail limits)
        p(t, frame, (50.0 + k * 4.0, 60.0 + k * 2.0), (0.02 * k, 0.01 * k), ["status"], z=0.03 * k)
    assert len(p.uvs) == 30 and len(p.trail) == 30
    assert p.trail[-1][3] == pytest.approx(0.03 * 29)
    panel = p._map(120, now=290.0)
    assert panel.shape == (120, 120, 3)
    assert p._center is not None and p._center[2] > 0.1
    # Key 'c' explicitly clears both persistent trails
    p._key(ord("c"), 300.0)
    assert len(p.uvs) == 0 and len(p.trail) == 0


def test_coral_tracker_landing_zoom_preserves_canonical_sensor_coordinates():
    from ground_station.analysis.live_tracker import CoralTracker
    target = (312, 228)
    frame = _coral_frame(target)
    t_base = CoralTracker(target, zoom=1.0, land_zoom=1.0)
    t_zoom = CoralTracker(target, zoom=1.0, land_zoom=2.0, land_z_m=0.30)
    uv_base = t_base.step(frame, z_agl=0.15)
    uv_zoom = t_zoom.step(frame, z_agl=0.15)
    assert uv_base is not None and uv_zoom is not None
    assert t_zoom.active_zoom == pytest.approx(2.0)
    assert np.allclose(uv_base, uv_zoom, atol=0.75)



def test_pip_label():
    from ground_station.analysis.live_tracker import pip_label
    assert pip_label(1.5, 0.45) == "LAND ZOOM 1.5x z=45cm"
    assert pip_label(3.0, 0.2) == "LAND ZOOM 3.0x z=20cm"

def test_preview_headless_does_not_call_cv2_window_functions(monkeypatch):
    from ground_station.analysis.live_tracker import Preview
    import cv2
    import numpy as np

    def raise_err(*args, **kwargs):
        raise RuntimeError("Window function called in headless mode!")

    monkeypatch.setattr(cv2, "namedWindow", raise_err)
    monkeypatch.setattr(cv2, "imshow", raise_err)
    monkeypatch.setattr(cv2, "resizeWindow", raise_err)
    monkeypatch.setattr(cv2, "moveWindow", raise_err)

    p = Preview(headless=True)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    p(1.0, frame, None, None, [])

def test_cli_flags_reach_coral_tracker_and_zoom_validated(monkeypatch, tmp_path):
    from ground_station.analysis.live_tracker import main
    import ground_station.analysis.live_tracker as lt

    captured_ring = None

    def mock_run_source(*args, **kwargs):
        nonlocal captured_ring
        captured_ring = kwargs.get("ring")
        return {}

    monkeypatch.setattr(lt, "run_source", mock_run_source)

    cam_json = tmp_path / "cam.json"
    cam_json.write_text('{"K": [[1,0,0], [0,1,0], [0,0,1]], "dist": [0,0,0,0,0], "R": [[1,0,0], [0,1,0], [0,0,1]], "t": [0,0,0], "aruco_id0_corners_px": [[0,0],[1,0],[1,1],[0,1]]}')

    main([
        "--source", "0",
        "--cam", str(cam_json),
        "--out", str(tmp_path / "out.csv"),
        "--detector", "coral",
        "--zoom", "1.5",
        "--land-zoom", "3.0",
        "--land-z-m", "0.4",
        "--height-m", "0.0"
    ])

    assert captured_ring is not None
    assert getattr(captured_ring, "zoom") == 1.5
    assert getattr(captured_ring, "land_zoom") == 3.0
    assert getattr(captured_ring, "land_z_m") == 0.4

def test_ab_replay_produces_summary_and_handles_flags_correctly(tmp_path):
    import cv2
    import json
    from ground_station.analysis.live_tracker import replay_main
    from ground_station.analysis.tests.test_live_tracker import _coral_frame
    p = str(tmp_path / "test_ab.avi")
    w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (640, 480))
    for k in range(30):
        w.write(_coral_frame((300 + k, 260)))
    w.release()

    cam = {"K": [[500.0, 0, 320], [0, 500.0, 240], [0, 0, 1]], "dist": [0.0] * 5,
           "R": [[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]], "t": [0.0, 0.0, 2.0]}
    with open(tmp_path / "cam.json", "w") as f:
        json.dump(cam, f)

    z_csv = tmp_path / "z.csv"
    with open(z_csv, "w") as f:
        f.write("t_s,z_m\n0.0,0.4\n0.5,0.2\n1.0,0.1\n")

    ret = replay_main([p, "--z-csv", str(z_csv)])
    assert ret == 0

    md_text = (tmp_path / "ab_summary.md").read_text()
    assert "A/B Replay Summary" in md_text
    assert "Detection Rate**: A =" in md_text
    assert "Zoom/Crop**:" in md_text
    assert "Draw**:" in md_text

    import pandas as pd
    df = pd.read_csv(tmp_path / "ab_frames.csv")
    assert "z_eff_b" in df.columns
    # Both runs detect the ring on at least 90% of frames
    lines = md_text.split("\n")
    det_line = next(line for line in lines if "Detection Rate**: A =" in line)
    a_rate = float(det_line.split("A = ")[1].split("%")[0])
    b_rate = float(det_line.split("B = ")[1].split("%")[0])
    assert a_rate >= 90.0 and b_rate >= 90.0

    # Zoom B is land_zoom exactly when z <= land_z_m and zoom when z > land_z_m
    # Test script used zoom=1.0, land_zoom=2.0, land_z_m=0.30
    assert (df.loc[df["z_eff_b"] == 2.0, "t_s"] > 0.0).any()
    z_m_list = [0.4, 0.2, 0.1]
    def get_z_m(t_s):
        import numpy as np
        ts_list = [0.0, 0.5, 1.0]
        return np.interp(t_s, ts_list, z_m_list)

    df["z_m"] = df["t_s"].apply(get_z_m)
    assert (df.loc[df["z_m"] <= 0.30, "z_eff_b"] == 2.0).all()
    assert (df.loc[df["z_m"] > 0.30, "z_eff_b"] == 1.0).all()
