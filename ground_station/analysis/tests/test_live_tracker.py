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
