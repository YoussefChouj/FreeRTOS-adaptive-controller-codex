import cv2
import numpy as np

from ground_station.analysis.video_truth import plumb_holds, ray_at_vertical, ring_height


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
