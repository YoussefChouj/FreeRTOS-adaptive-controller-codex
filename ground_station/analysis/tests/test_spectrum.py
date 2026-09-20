"""Tests for ground_station.analysis.spectrum.

The known-sine tests are the credibility anchor for the hand-written FFT:
a sine whose frequency lands exactly on a bin MUST peak in that bin at its
constructed amplitude, under every offered window.
"""
from __future__ import annotations

import math

import pytest

from ground_station.analysis.session import (
    compute_effective_rate,
    compute_gaps,
    compute_jitter,
)
from ground_station.analysis.spectrum import (
    DEFAULT_MAX_SAMPLES,
    SpectrumResult,
    compute_spectrum,
    fft_one_sided,
    fft_radix2,
)
from ground_station.service.storage import SessionStore


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _sine(n: int, fs: float, freq: float, amplitude: float = 1.0,
          phase: float = 0.0) -> list[float]:
    return [amplitude * math.sin(2.0 * math.pi * freq * i / fs + phase)
            for i in range(n)]


def _seed_uniform(store: SessionStore, n: int, key: str = "x",
                  stream_id: int = 1, *, fs: float = 50.0,
                  values_fn=None, source_clock: bool = True,
                  gap_after: int | None = None,
                  gap_ms: float = 200.0) -> str:
    """Seed n samples at fs Hz (20 ms at 50 Hz), optionally with one gap.

    time_ns and source_time_ms stay in lockstep unless source_clock=False
    (source_time_ms stored as None) or gap_after is set (both clocks see the
    gap at the same index).
    """
    sid = store.start_session("test-spectrum", source="test")
    dt_ms = 1000.0 / fs
    t0_ns = 2_000_000_000
    t0_ms = 10_000
    for i in range(n):
        step = i
        if gap_after is not None and i > gap_after:
            step += int(round(gap_ms / dt_ms)) - 1
        t_ns = t0_ns + int(round(step * dt_ms * 1_000_000))
        s_ms = int(round(t0_ms + step * dt_ms)) if source_clock else None
        val = values_fn(i) if values_fn is not None else float(i)
        store.append_telemetry(sid, stream_id=stream_id, sequence=i,
                               values={key: val},
                               source_time_ms=s_ms, time_ns=t_ns)
    return sid


# ----------------------------------------------------------------------
# Transform-level tests (the FFT itself)
# ----------------------------------------------------------------------

class TestFFTKnownSignal:
    def test_sine_peaks_in_exact_bin_rectangular(self):
        # 512 samples at 256 Hz: 8 Hz is exactly bin 16 (resolution 0.5 Hz).
        fs, n, f = 256.0, 512, 8.0
        freqs, mags, nfft = fft_one_sided(_sine(n, fs, f), fs, "none")
        peak = max(range(1, len(mags)), key=lambda k: mags[k])
        assert nfft == 512
        assert freqs[peak] == pytest.approx(8.0, abs=1e-12)
        assert mags[peak] == pytest.approx(1.0, abs=1e-9)

    def test_sine_peaks_in_exact_bin_hann(self):
        # Same signal through a Hann window: coherent-gain correction must
        # restore the amplitude despite the window.
        fs, n, f = 256.0, 512, 8.0
        freqs, mags, _ = fft_one_sided(_sine(n, fs, f), fs, "hann")
        peak = max(range(1, len(mags)), key=lambda k: mags[k])
        assert freqs[peak] == pytest.approx(8.0, abs=1e-12)
        assert mags[peak] == pytest.approx(1.0, abs=1e-6)

    def test_sine_hamming_window(self):
        fs, n, f = 100.0, 256, 10.0  # 10 Hz = bin ? 10/(100/256)=25.6 -> not exact
        freqs, mags, _ = fft_one_sided(_sine(n, fs, f), fs, "hamming")
        peak = max(range(1, len(mags)), key=lambda k: mags[k])
        # nearest bin to 10 Hz is bin 26 -> 10.15625 Hz
        assert freqs[peak] == pytest.approx(10.15625, abs=1e-9)

    def test_one_sided_extent_and_nyquist(self):
        fs, n = 256.0, 300  # non-power-of-two -> padded to 512
        freqs, mags, nfft = fft_one_sided(_sine(n, fs, 8.0), fs, "none")
        assert nfft == 512
        assert len(freqs) == len(mags) == 257  # 0..N/2 inclusive
        assert freqs[0] == 0.0
        assert freqs[-1] == pytest.approx(fs / 2)
        assert freqs[1] == pytest.approx(fs / 512)

    def test_dc_bin_is_amplitude_and_peak_excludes_it(self):
        values = [3.0] * 64
        freqs, mags, _ = fft_one_sided(values, 64.0, "none")
        assert mags[0] == pytest.approx(3.0, abs=1e-12)
        peak = max(range(1, len(mags)), key=lambda k: mags[k])
        assert mags[peak] < 1e-12

    def test_radix2_rejects_non_power_of_two(self):
        with pytest.raises(ValueError):
            fft_radix2([1.0, 2.0, 3.0])

    def test_fft_too_few_samples(self):
        with pytest.raises(ValueError):
            fft_one_sided([1.0], 10.0, "none")

    def test_bad_window_name(self):
        with pytest.raises(ValueError):
            fft_one_sided(_sine(64, 64.0, 4.0), 64.0, "blackman")

    def test_dft_agreement_on_tiny_signal(self):
        # Independent O(n^2) reference DFT cross-check on an arbitrary signal.
        values = [0.5, -1.25, 2.0, 0.75, -0.25, 1.5, -1.0, 0.1]
        n = len(values)
        fast = fft_radix2(values)
        for k in range(n):
            re = sum(values[t] * math.cos(2.0 * math.pi * k * t / n)
                     for t in range(n))
            im = -sum(values[t] * math.sin(2.0 * math.pi * k * t / n)
                      for t in range(n))
            assert fast[k].real == pytest.approx(re, abs=1e-10)
            assert fast[k].imag == pytest.approx(im, abs=1e-10)


# ----------------------------------------------------------------------
# Store-level compute_spectrum tests
# ----------------------------------------------------------------------

class TestComputeSpectrum:
    def test_clean_50hz_sine_full_metadata(self):
        store = SessionStore()
        n, fs = 256, 50.0
        f = 10.0 * fs / n  # exactly bin 10 -> 1.953125 Hz
        sid = _seed_uniform(store, n, values_fn=lambda i: math.sin(
            2.0 * math.pi * f * i / fs))
        r = compute_spectrum(store, sid, 1, "x")
        assert isinstance(r, SpectrumResult)
        assert r.samples_used == r.raw_samples == 256
        assert r.fft_size == 256
        assert r.size_adjustment == "none"
        assert r.sample_rate_source == "source_time_ms"
        assert r.sample_rate_hz == pytest.approx(50.0, abs=1e-9)
        assert r.frequency_resolution_hz == pytest.approx(50.0 / 256)
        assert r.nyquist_hz == pytest.approx(25.0)
        assert r.window == "hann"
        assert r.peak_frequency_hz == pytest.approx(1.953125, abs=1e-12)
        assert r.peak_magnitude == pytest.approx(1.0, abs=1e-6)
        assert r.has_gaps is False
        assert r.flagged is False
        assert r.gap_count == 0
        assert r.clock_wrap_count == 0
        assert "one-sided amplitude" in r.magnitude_convention
        assert len(r.frequencies_hz) == len(r.magnitudes) == 129

    def test_gap_window_is_flagged_not_silent(self):
        store = SessionStore()
        n = 200
        sid = _seed_uniform(store, n, gap_after=100, gap_ms=200.0)
        r = compute_spectrum(store, sid, 1, "x", window="none")
        assert r.has_gaps is True
        assert r.flagged is True
        assert r.gap_count == 1
        assert r.gaps[0]["dt_ns"] == 200_000_000
        assert any("gap" in w for w in r.warnings)
        assert r.window == "none"
        # jitter reflects the missing-frame event too
        assert r.jitter["jitter_mean_ns"] > 0
        # agrees with the imported session helper
        assert compute_gaps(store, sid, 1)[1][0]["dt_ns"] == 200_000_000

    def test_window_bounds_exclude_gap_not_flagged(self):
        # Same gappy session, but analyse only the clean tail: window-scoped
        # statistics must be computed locally (session helpers are full-session).
        store = SessionStore()
        n = 200
        sid = _seed_uniform(store, n, gap_after=100, gap_ms=200.0)
        since_ns = 2_000_000_000 + 130 * 20_000_000
        r = compute_spectrum(store, sid, 1, "x", since_ns=since_ns)
        assert r.samples_used > 2
        assert r.has_gaps is False
        assert r.flagged is False
        assert r.gap_count == 0
        assert r.start_ns == since_ns

    def test_caller_rate_override_provenance(self):
        store = SessionStore()
        n, fs = 256, 50.0
        f = 10.0 * fs / n
        sid = _seed_uniform(store, n, values_fn=lambda i: math.sin(
            2.0 * math.pi * f * i / fs))
        r = compute_spectrum(store, sid, 1, "x", sample_rate_hz=100.0)
        assert r.sample_rate_source == "caller"
        assert r.sample_rate_hz == 100.0
        assert r.nyquist_hz == 50.0
        # same physical bin 10, but interpreted at 100 Hz -> 3.90625 Hz
        assert r.peak_frequency_hz == pytest.approx(3.90625, abs=1e-12)

    def test_wall_clock_fallback_when_source_clock_missing(self):
        store = SessionStore()
        sid = _seed_uniform(store, 128, source_clock=False)
        r = compute_spectrum(store, sid, 1, "x")
        assert r.sample_rate_source == "wall_clock:time_ns"
        assert r.sample_rate_hz == pytest.approx(50.0, abs=1e-9)
        assert any("wall-clock" in w for w in r.warnings)

    def test_session_effective_rate_fallback_for_short_window(self):
        # Rows INSIDE the window carry no firmware clock, but earlier rows of
        # the same stream do -> fall back to compute_effective_rate.
        store = SessionStore()
        sid = store.start_session("test-spectrum", source="test")
        for i in range(100):
            store.append_telemetry(
                sid, stream_id=1, sequence=i, values={"x": float(i)},
                source_time_ms=10_000 + i * 20,
                time_ns=2_000_000_000 + i * 20_000_000)
        for i in range(100, 104):
            store.append_telemetry(
                sid, stream_id=1, sequence=i, values={"x": float(i)},
                source_time_ms=None,
                time_ns=2_000_000_000 + i * 20_000_000)
        since_ns = 2_000_000_000 + 100 * 20_000_000
        r = compute_spectrum(store, sid, 1, "x", since_ns=since_ns)
        assert r.sample_rate_source == "source_time_ms:session_effective_rate"
        assert r.sample_rate_hz == pytest.approx(50.0, abs=1e-9)

    def test_zero_padding_reported(self):
        store = SessionStore()
        sid = _seed_uniform(store, 300)
        r = compute_spectrum(store, sid, 1, "x")
        assert r.samples_used == 300
        assert r.fft_size == 512
        assert r.size_adjustment == "zero-padded"
        assert r.frequency_resolution_hz == pytest.approx(50.0 / 512)
        assert any("zero-padded" in w for w in r.warnings)

    def test_truncation_cap_reported(self):
        store = SessionStore()
        sid = _seed_uniform(store, 100)
        r = compute_spectrum(store, sid, 1, "x", max_samples=64)
        assert r.raw_samples == 100
        assert r.samples_used == 64
        assert r.fft_size == 64
        assert r.size_adjustment == "truncated"
        assert any("truncated" in w for w in r.warnings)

    def test_default_cap_is_power_of_two(self):
        # The default cap must itself be radix-2 friendly.
        assert DEFAULT_MAX_SAMPLES & (DEFAULT_MAX_SAMPLES - 1) == 0

    def test_jitter_agrees_with_session_helper_on_clean_stream(self):
        store = SessionStore()
        sid = _seed_uniform(store, 128)
        r = compute_spectrum(store, sid, 1, "x")
        helper = compute_jitter(store, sid, 1)[1]
        assert r.jitter["jitter_mean_ns"] == helper["jitter_mean_ns"] == 0
        assert r.jitter["jitter_max_ns"] == 0

    def test_clock_wrap_excluded_from_rate(self):
        # Firmware clock starts just below the uint32 ms wrap point and wraps
        # exactly once; receiver time_ns and true spacing stay uniform 20 ms.
        store = SessionStore()
        sid = store.start_session("test-spectrum", source="test")
        t0_ms = 2**32 - 2_000
        for i in range(256):
            s_ms = (t0_ms + i * 20) & 0xFFFFFFFF  # wrapping uint32 ms clock
            store.append_telemetry(
                sid, stream_id=1, sequence=i, values={"x": float(i)},
                source_time_ms=s_ms,
                time_ns=2_000_000_000 + i * 20_000_000)
        r = compute_spectrum(store, sid, 1, "x")
        assert r.clock_wrap_count == 1
        assert r.sample_rate_hz == pytest.approx(50.0, abs=1e-9)
        assert compute_effective_rate(store, sid, 1)[1]["clock_wrap_count"] == 1

    def test_missing_key_raises(self):
        store = SessionStore()
        sid = _seed_uniform(store, 16)
        with pytest.raises(ValueError, match="at least 2 numeric samples"):
            compute_spectrum(store, sid, 1, "absent")

    def test_non_numeric_field_raises(self):
        store = SessionStore()
        sid = store.start_session("test-spectrum", source="test")
        for i in range(8):
            store.append_telemetry(
                sid, stream_id=1, sequence=i, values={"mode": "stabilize"},
                source_time_ms=10_000 + i * 20,
                time_ns=2_000_000_000 + i * 20_000_000)
        with pytest.raises(ValueError, match="not numeric"):
            compute_spectrum(store, sid, 1, "mode")

    def test_bad_window_and_bad_cap_raise(self):
        store = SessionStore()
        sid = _seed_uniform(store, 16)
        with pytest.raises(ValueError):
            compute_spectrum(store, sid, 1, "x", window="nope")
        with pytest.raises(ValueError):
            compute_spectrum(store, sid, 1, "x", max_samples=1)
