"""Supervisor-owned contract edge cases for metrics.py (WP2 brief, section A).

Independent of the worker's own tests: each case pins one sentence of the contract.
"""
import numpy as np
import pytest

from ground_station.analysis.flightlab import metrics as m

FS = 100.0
T = np.arange(0.0, 20.0, 1.0 / FS)


@pytest.mark.parametrize("nperseg", [64, 65])
def test_welch_matches_scipy_even_and_odd(nperseg):
    ss = pytest.importorskip("scipy.signal")
    x = np.random.default_rng(0).standard_normal(1001)
    f1, p1 = m.welch_psd(x, FS, nperseg)
    f2, p2 = ss.welch(x, FS, window="hann", nperseg=nperseg, noverlap=nperseg // 2,
                      detrend="constant", scaling="density")
    assert np.allclose(f1, f2) and np.allclose(p1, p2, rtol=1e-9)


def test_xcorr_sign_and_degenerate():
    a = np.sin(2 * np.pi * 1.3 * T) + 0.3 * np.sin(2 * np.pi * 3.7 * T)
    b = np.r_[np.zeros(5), a[:-5]]
    assert m.xcorr_lag_s(a, b, FS, 0.5) == pytest.approx(0.05, abs=1 / FS)
    assert m.xcorr_lag_s(b, a, FS, 0.5) == pytest.approx(-0.05, abs=1 / FS)
    assert np.isnan(m.xcorr_lag_s(np.ones(50), a[:50], FS, 0.5))


def test_gain_phase_and_nan_cases():
    des = np.sin(2 * np.pi * T)
    fb = 0.5 * np.sin(2 * np.pi * (T - 0.05))
    g, ph = m.gain_phase_at(des, fb, FS, 1.0)
    assert g == pytest.approx(0.5, rel=0.05) and ph == pytest.approx(-18.0, abs=3.0)
    assert all(np.isnan(m.gain_phase_at(np.zeros(500), fb[:500], FS, 1.0)))
    assert all(np.isnan(m.gain_phase_at([1.0], [1.0], FS, 1.0)))
    assert all(np.isnan(m.gain_phase_at(des, fb, FS, 80.0)))  # j = 4 > nperseg//2 = 2


def test_settle_lowpass_wrap():
    assert m.settle_time(T, np.ones_like(T)) == 0.0
    y = np.r_[np.zeros(100), np.ones(900)]
    assert m.settle_time(T[:1000], y) == pytest.approx(1.0)
    yl = m.lowpass_1pole(np.array([1.0, np.nan, 3.0]), FS, 1.0)
    assert yl[0] == 1.0 and yl[1] == yl[0] and yl[2] > yl[1]
    assert m.wrap_deg(190) == -170 and m.wrap_deg(180) == -180


def test_intervals_and_stats():
    assert m.longest_interval([(0, 2), (5, 7)]) == (0, 2)
    assert m.longest_interval([]) is None
    assert list(m.segment_mask(np.array([0.0, 1.0, 2.0]), [(1.0, 2.0)])) == [False, True, False]
    assert m.std([1.0, 2.0, 3.0, np.nan]) == pytest.approx(np.std([1, 2, 3]))
    assert m.p95_abs([-10.0, 1.0, np.nan]) == pytest.approx(np.percentile([10, 1], 95))
    assert np.isnan(m.rms([np.nan]))


def test_peaks_and_band_power():
    f = np.arange(7.0)
    p = np.array([0, 5, 1, 3, 1, 4, 0.0])
    assert [q["hz"] for q in m.psd_peaks(f, p, 1.0, None, 2)] == [1.0, 5.0]
    assert [q["hz"] for q in m.psd_peaks(f, p, 1.0, 4.0, 5)] == [1.0, 3.0]
    assert m.band_power(f, p, 1, 4) == pytest.approx(np.trapezoid(p[1:4], f[1:4]))
    assert np.isnan(m.band_power(f, p, 1, 2))
