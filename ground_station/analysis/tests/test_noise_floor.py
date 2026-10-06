import numpy as np

from ground_station.analysis.noise_floor import allan, fast_segment, main, psd


def test_fast_segment_picks_the_dense_stretch():
    slow = np.arange(0, 5, 0.01)            # 100 Hz for 5 s
    fast = 5 + np.arange(0, 10, 0.005)       # 200 Hz for 10 s
    t = np.concatenate([slow, fast])
    seg = fast_segment(t, 150)
    assert t[seg][0] >= 5.0 and t[seg][-1] > 14.9


def test_slow_log_reports_no_segment_instead_of_crashing(tmp_path):
    p = tmp_path / "slow.csv"
    t = np.arange(0, 5, 0.01)
    p.write_text("t,ORI_Gyrox\n" + "".join(f"{v},{0.1}\n" for v in t))
    assert main([str(p), "--min-hz", "150"]) == 1


def test_allan_of_white_noise_matches_sigma_at_one_sample():
    rng = np.random.default_rng(0)
    fs, sigma = 200.0, 2.0
    x = rng.normal(0.0, sigma, 40000)
    taus, adev = allan(x, fs)
    assert abs(taus[0] - 1 / fs) < 1e-12
    assert abs(adev[0] - sigma) < 0.05 * sigma
    # white noise falls as 1/sqrt(tau)
    k = np.searchsorted(taus, 1.0)
    assert abs(adev[k] / adev[0] - np.sqrt(taus[0] / taus[k])) < 0.1


def test_psd_finds_a_tone():
    fs = 200.0
    t = np.arange(0, 20, 1 / fs)
    f, p = psd(np.sin(2 * np.pi * 37.5 * t), fs)
    assert abs(f[np.argmax(p)] - 37.5) < fs / 512
