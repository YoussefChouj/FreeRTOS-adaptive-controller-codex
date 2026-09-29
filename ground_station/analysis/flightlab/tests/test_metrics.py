import numpy as np
import pytest
import math
from ground_station.analysis.flightlab import metrics

def test_metrics_basics():
    x = [1, 2, np.nan, 4, 5]
    assert np.isnan(metrics.rms([]))
    assert np.isclose(metrics.rms(x), np.sqrt(np.mean([1, 4, 16, 25])))
    assert np.isclose(metrics.mean(x), 3.0)
    assert np.isclose(metrics.std(x), np.std([1, 2, 4, 5], ddof=0))
    assert np.isclose(metrics.p95_abs(x), np.percentile([1, 2, 4, 5], 95))
    assert np.isclose(metrics.max_abs(x), 5.0)

    mask = [True, False, True, True]
    assert np.isclose(metrics.frac_true(mask), 0.75)
    assert np.isnan(metrics.frac_true([]))

def test_integral():
    t = [0, 1, 2, 3]
    e = [1, 1, np.nan, 2]
    # valid t: [0, 1, 3], e: [1, 1, 2]
    # iae: trapz([1, 1, 2], [0, 1, 3]) -> area under (0,1), (1,1), (3,2).
    # intervals: (1-0)*1 = 1.0 (wait, trapz is average height. (1+1)/2 * 1 = 1.0)
    # (3-1)* (1+2)/2 = 2 * 1.5 = 3.0. Sum = 4.0
    assert np.isclose(metrics.iae(t, e), 4.0)

    # itae: (t-t0) * |e| -> [0, 1, 3] * [1, 1, 2] = [0, 1, 6]
    # trapz([0, 1, 6], [0, 1, 3]) -> (1-0)*(0+1)/2 = 0.5.
    # (3-1)*(1+6)/2 = 2 * 3.5 = 7.0. Sum = 7.5
    assert np.isclose(metrics.itae(t, e), 7.5)

def test_welch():
    pytest.importorskip("scipy.signal")
    from scipy import signal
    fs = 100
    x = np.random.RandomState(0).randn(1000)
    x[10] = np.nan # test non-finite dropping
    nperseg = 256
    
    f, p = metrics.welch_psd(x, fs, nperseg)
    x_valid = x[np.isfinite(x)]
    f2, p2 = signal.welch(x_valid, fs, window="hann", nperseg=nperseg, noverlap=nperseg // 2, detrend="constant", scaling="density")
    
    assert np.allclose(f, f2, rtol=1e-9)
    assert np.allclose(p, p2, rtol=1e-9)

def test_psd_peaks():
    f = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    p = np.array([0.0, 10.0, 1.0, 20.0, 1.0, 5.0, 0.0])
    # peaks at f=1 (p=10), f=3 (p=20), f=5 (p=5)
    # order should be 3, 1, 5
    peaks = metrics.psd_peaks(f, p, 0.5, 4.5, 2)
    assert len(peaks) == 2
    assert peaks[0]["hz"] == 3.0
    assert peaks[0]["psd"] == 20.0
    assert peaks[1]["hz"] == 1.0
    assert peaks[1]["psd"] == 10.0

def test_band_power():
    f = np.array([0.0, 1.0, 2.0, 3.0])
    p = np.array([1.0, 2.0, 2.0, 1.0])
    # lo=0.5, hi=2.5 -> f in [1.0, 2.0], p in [2.0, 2.0]
    # trapz([2.0, 2.0], [1.0, 2.0]) -> 2.0
    assert np.isclose(metrics.band_power(f, p, 0.5, 2.5), 2.0)

def test_xcorr_lag():
    fs = 100
    a = np.random.RandomState(0).randn(1000)
    # b is delayed by 0.05s -> lag is 5 samples
    b = np.roll(a, 5)
    b[:5] = 0
    
    lag = metrics.xcorr_lag_s(a, b, fs, max_lag_s=0.2)
    assert lag == pytest.approx(0.05, abs=1/fs)

def test_gain_phase():
    fs = 100
    t = np.arange(0, 5.0, 1/fs)
    f0 = 1.0
    des = np.sin(2 * np.pi * f0 * t)
    # fb = 0.5 * des delayed by 0.05s
    # 0.05s at 1Hz = 0.05 cycles = 0.05 * 360 = 18 degrees delay -> -18 degrees
    fb = 0.5 * np.sin(2 * np.pi * f0 * (t - 0.05))
    
    gain, phase = metrics.gain_phase_at(des, fb, fs, f0)
    assert gain == pytest.approx(0.5, rel=0.05)
    assert phase == pytest.approx(-18.0, abs=3.0)

def test_linear_slope():
    t = [0, 1, 2]
    y = [1, 3, 5]
    assert np.isclose(metrics.linear_slope(t, y), 2.0)

def test_settle_time():
    t = np.arange(0, 10.0, 0.1)
    y = 1.0 - np.exp(-t)
    # y_final ~ 1.0. band = 0.1 * 1.0 = 0.1
    # within band when |y - 1| <= 0.1 -> exp(-t) <= 0.1 -> t >= -ln(0.1) ~ 2.3
    st = metrics.settle_time(t, y, frac=0.1)
    assert st == pytest.approx(2.3, abs=0.2)

def test_wrap_deg():
    assert metrics.wrap_deg(190) == -170
    assert metrics.wrap_deg(-190) == 170

def test_lowpass():
    fs = 100
    fc = 1.0
    x = np.ones(1000)
    x[0] = 0.0 # step at t=0
    y = metrics.lowpass_1pole(x, fs, fc)
    
    target_t = 1 / (2 * np.pi * fc)
    target_idx = int(round(target_t * fs))
    
    assert y[target_idx] == pytest.approx(1 - np.exp(-1), rel=0.05)

def test_segments():
    t = np.arange(10)
    intervals = [(2, 5), (7, 9)]
    mask = metrics.segment_mask(t, intervals)
    assert np.all(mask == [0, 0, 1, 1, 1, 0, 0, 1, 1, 0])
    
    best = metrics.longest_interval(intervals)
    assert best == (2, 5)
