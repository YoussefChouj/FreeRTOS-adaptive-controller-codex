"""live_tracker: earth alignment fit, fix scheduler (median, spread, period), frame packing."""
import numpy as np
import pytest

from ground_station.analysis.live_tracker import EarthAlign, FixScheduler
from ground_station.comm.ext_pos_encoder import ext_pos_decode
import struct


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


def test_jump_gate_rejects_jumps_caps_dt_and_reseeds_on_a_cluster():
    from ground_station.analysis.live_tracker import JumpGate
    g = JumpGate(v_max=1.0, slack_m=0.05, dt_max=0.5, n_reseed=3, cluster_m=0.05)
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
