"""FFT spectrum analysis for one numeric field of one telemetry stream.

WP5 clause (b): "Time series and FFT use sample timestamps and report jitter,
gaps, effective rate, and window."

Non-uniform sampling decision
-----------------------------
This telemetry is **not** uniformly sampled: ``session.compute_jitter`` and
``session.compute_gaps`` exist precisely because frame spacing varies and
frames go missing.  A DFT/FFT nevertheless assumes a constant sample interval.
Of the two honest options offered by the WP5 spec, this module chooses
**option (a): treat the samples as uniform at the measured effective rate and
return the jitter/gap metadata alongside so the caller can judge the result.**

Reasoning:

* The firmware ``source_time_ms`` clock gives a real per-stream effective
  rate (median source-clock delta, clock wraps excluded — exactly the rule in
  ``session.compute_effective_rate``).
* Resampling (option b) would interpolate values the drone never sent; with a
  gappy link that interpolation is itself fiction on top of the same irregular
  grid, plus it changes the signal's amplitude statistics.
* Treating the data as uniform WITHOUT reporting the sampling quality is the
  one option that is not defensible.  So every :class:`SpectrumResult` carries
  the rate used, where that rate came from, the window, the transform size
  (including zero-pad/truncate adjustments), the frequency resolution, the
  Nyquist limit, and the jitter/gap counts of the exact samples transformed.
  A window containing gaps is explicitly flagged (``flagged=True``) rather
  than returned as if it were clean.

The transform itself is a pure-Python iterative radix-2 Cooley-Tukey FFT
(standard library only — no numpy/scipy).  Inputs longer than ``max_samples``
are truncated to the first ``max_samples`` samples; non-power-of-two lengths
are zero-padded to the next power of two.  Both actions are reported in the
result metadata.  The returned spectrum is one-sided (bins 0..Nyquist).
"""
from __future__ import annotations

import cmath
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Sequence

from ground_station.analysis.session import (
    compute_effective_rate,
    compute_gaps,
    compute_jitter,
    query_telemetry,
)
from ground_station.service.storage import SessionStore

#: Window functions supported by :func:`fft_one_sided` / :func:`compute_spectrum`.
WINDOWS = ("none", "hann", "hamming")

#: Hard default cap on the number of samples fed to the pure-Python FFT.
DEFAULT_MAX_SAMPLES = 4096

_GAP_THRESHOLD_MULTIPLIER = 5.0  # same definition as session.compute_gaps

_MAGNITUDE_CONVENTION = (
    "one-sided amplitude: 2*abs(X[k])/sum(window) for 1<=k<N/2; "
    "DC and Nyquist bins un-doubled; window coherent-gain corrected"
)


@dataclass(frozen=True)
class SpectrumResult:
    """A spectrum plus the honesty metadata describing how it was produced.

    ``frequencies_hz`` and ``magnitudes`` are aligned one-to-one and contain
    the one-sided bins 0..``nyquist_hz`` inclusive (``fft_size/2 + 1`` bins).
    """
    stream_id: int
    field: str
    start_ns: int | None
    end_ns: int | None
    frequencies_hz: tuple[float, ...]
    magnitudes: tuple[float, ...]
    #: Number of real samples actually transformed (before zero-padding).
    samples_used: int
    #: Number of matching numeric samples found before any truncation.
    raw_samples: int
    #: Transform length actually fed to the FFT (>= samples_used if padded).
    fft_size: int
    #: "none" | "zero-padded" | "truncated" | "truncated-and-zero-padded"
    size_adjustment: str
    sample_rate_hz: float
    #: "caller" | "source_time_ms" | "source_time_ms:session_effective_rate"
    #: | "wall_clock:time_ns"
    sample_rate_source: str
    frequency_resolution_hz: float
    nyquist_hz: float
    window: str
    magnitude_convention: str
    #: Strongest non-DC bin.
    peak_frequency_hz: float | None
    peak_magnitude: float
    #: Same keys as ``session.compute_jitter``, for the samples transformed.
    jitter: dict[str, float | None]
    gap_count: int
    #: Gap events, same shape as ``session.compute_gaps`` entries.
    gaps: tuple[dict[str, Any], ...]
    #: firmware-clock wraps observed among the transformed samples
    clock_wrap_count: int
    #: True when at least one gap (dt > 5x median dt) is inside the window.
    has_gaps: bool
    #: True whenever the spectrum must not be read as a clean uniform spectrum.
    flagged: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _next_power_of_two(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def _window_weights(n: int, window: str) -> tuple[list[float], float]:
    """Return (weights, sum) for an n-sample analysis window."""
    if window == "none":
        return [1.0] * n, float(n)
    if n == 1:
        return [1.0], 1.0
    # Periodic windows (denominator n, not n-1): these are the standard
    # windows for spectral analysis and reconstruct an integer-cycle tone
    # exactly (its modulation lands precisely on the adjacent bins).
    if window == "hann":
        w = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n)
             for i in range(n)]
    elif window == "hamming":
        w = [0.54 - 0.46 * math.cos(2.0 * math.pi * i / n)
             for i in range(n)]
    else:
        raise ValueError(f"unknown window {window!r}; expected one of {WINDOWS}")
    return w, sum(w)


def fft_radix2(x: Sequence[complex | float]) -> list[complex]:
    """In-place-style iterative radix-2 Cooley-Tukey FFT.

    ``len(x)`` must be a power of two.  Returns the complex DFT with the
    standard unnormalised forward convention (divide by N for amplitudes).
    Pure standard library (``cmath``).
    """
    n = len(x)
    if n == 0:
        return []
    if n & (n - 1):
        raise ValueError(f"fft_radix2 requires a power-of-two length, got {n}")
    a: list[complex] = [complex(v) for v in x]
    # Bit-reversal permutation.
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    # Butterfly passes.
    length = 2
    while length <= n:
        half = length >> 1
        wlen = cmath.exp(-2j * math.pi / length)
        for start in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * w
                a[start + k] = u + v
                a[start + k + half] = u - v
                w *= wlen
        length <<= 1
    return a


def fft_one_sided(values: Sequence[float], sample_rate_hz: float,
                  window: str = "hann"
                  ) -> tuple[tuple[float, ...], tuple[float, ...], int]:
    """Compute the one-sided amplitude spectrum of an equispaced real signal.

    Args:
        values: real samples (any length; zero-padded to a power of two).
        sample_rate_hz: sample rate assumed for the samples.
        window: "none", "hann" or "hamming".

    Returns:
        ``(frequencies_hz, magnitudes, fft_size)`` with ``fft_size/2 + 1``
        bins spanning 0..``sample_rate_hz/2``.  Magnitudes are window
        coherent-gain corrected one-sided amplitudes, so a sine of amplitude
        A whose frequency lands on a bin returns magnitude A at that bin.
    """
    n = len(values)
    if n < 2:
        raise ValueError("fft_one_sided needs at least 2 samples")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    nfft = _next_power_of_two(n)
    weights, wsum = _window_weights(n, window)
    framed = [float(v) * weights[i] for i, v in enumerate(values)]
    framed.extend([0.0] * (nfft - n))
    spectrum = fft_radix2(framed)
    half = nfft >> 1
    freqs = tuple(k * sample_rate_hz / nfft for k in range(half + 1))
    mags: list[float] = [abs(spectrum[0]) / wsum]  # DC, un-doubled
    for k in range(1, half):
        mags.append(2.0 * abs(spectrum[k]) / wsum)
    mags.append(abs(spectrum[half]) / wsum)  # Nyquist, un-doubled
    return freqs, tuple(mags), nfft


def _jitter_and_gaps(rows: Sequence[dict[str, Any]]
                     ) -> tuple[dict[str, float | None], list[dict[str, Any]], int]:
    """Jitter/gap statistics with the same definitions as ``session``.

    jitter = |dt - median_dt|; a gap is dt > 5 x median_dt; both on the
    receiver ``time_ns`` deltas, exactly as in ``compute_jitter`` /
    ``compute_gaps``.
    """
    dts: list[int] = []
    events: list[tuple[int, int, int, int]] = []
    for i in range(1, len(rows)):
        dt = int(rows[i]["time_ns"]) - int(rows[i - 1]["time_ns"])
        dts.append(dt)
        events.append((dt, int(rows[i]["time_ns"]),
                       int(rows[i]["sequence"]), int(rows[i - 1]["sequence"])))
    if len(dts) < 2:
        return ({"jitter_mean_ns": None, "jitter_std_ns": None,
                 "jitter_max_ns": None, "jitter_median_ns": None}, [], 0)
    median_ns = statistics.median(dts)
    jitters = [abs(dt - median_ns) for dt in dts]
    jitter = {
        "jitter_mean_ns": statistics.mean(jitters),
        "jitter_std_ns": (statistics.stdev(jitters)
                          if len(jitters) > 1 else None),
        "jitter_max_ns": max(jitters),
        "jitter_median_ns": statistics.median(jitters),
    }
    threshold = median_ns * _GAP_THRESHOLD_MULTIPLIER
    gaps = [
        {"time_ns": t, "dt_ns": dt, "sequence": seq, "prev_sequence": prev_seq}
        for dt, t, seq, prev_seq in events
        if dt > threshold
    ]
    return jitter, gaps, int(median_ns)


def _source_clock_rate(rows: Sequence[dict[str, Any]]) -> tuple[float | None, int]:
    """Rate from firmware source_time_ms deltas, reusing session.py's wrap rule.

    Negative deltas are clock wraps (source_time_ms is a wrapping uint32 ms
    clock): counted and excluded.  This mirrors compute_effective_rate and
    telemetry_stats rather than inventing a different wrap policy.
    """
    deltas: list[int] = []
    wraps = 0
    prev: float | None = None
    for row in rows:
        st = row.get("source_time_ms")
        if st is None:
            continue
        if prev is not None:
            dt_ms = int(st) - int(prev)
            if dt_ms < 0:
                wraps += 1
            else:
                deltas.append(dt_ms)
        prev = float(st)
    if len(deltas) < 2:
        return None, wraps
    median_ms = statistics.median(deltas)
    if not median_ms:
        return None, wraps
    return 1000.0 / median_ms, wraps


def _wall_clock_rate(rows: Sequence[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    dts = [int(rows[i]["time_ns"]) - int(rows[i - 1]["time_ns"])
           for i in range(1, len(rows))]
    median_ns = statistics.median(dts)
    return (1e9 / median_ns) if median_ns else None


def compute_spectrum(store: SessionStore, session_id: str,
                     stream_id: int, key: str, *,
                     since_ns: int | None = None,
                     until_ns: int | None = None,
                     window: str = "hann",
                     sample_rate_hz: float | None = None,
                     max_samples: int = DEFAULT_MAX_SAMPLES) -> SpectrumResult:
    """Compute the FFT spectrum of one numeric ``key`` of one stream.

    Follows the ``(store, session_id, stream_id=..., since_ns=...,
    until_ns=...)`` convention of :func:`session.query_telemetry`.

    Sample rate resolution order (reported verbatim in
    ``sample_rate_source``):

    1. ``sample_rate_hz`` caller override              -> "caller"
    2. median firmware-clock delta of the samples used -> "source_time_ms"
       (wrapping uint32 clock; negative deltas counted as wraps and excluded)
    3. :func:`session.compute_effective_rate` for the whole stream
       (used when the window itself contains too few clock deltas)
       -> "source_time_ms:session_effective_rate"
    4. median receiver ``time_ns`` delta               -> "wall_clock:time_ns"

    Jitter and gaps are reported for the **exact samples transformed**.  When
    no time window is given they come straight from
    :func:`session.compute_jitter` / :func:`session.compute_gaps` (a stream
    carries a fixed payload, so key filtering does not change its spacing);
    when a window is given those session-scoped helpers cannot be applied to
    a sub-range, so the identical statistics are computed on the windowed
    rows instead.

    A window containing one or more gaps comes back with ``has_gaps=True``
    and ``flagged=True``; the spectrum is still returned (with ``warnings``)
    so a caller can deliberately inspect it, but it is never presented as
    clean.

    Args:
        store: Session store.
        session_id: Session to query.
        stream_id: Stream to analyse.
        key: Numeric values-JSON field to transform.
        since_ns: Inclusive window start in receiver nanoseconds.
        until_ns: Exclusive window end.
        window: "none", "hann" (default) or "hamming".
        sample_rate_hz: Caller-supplied rate; skips rate estimation.
        max_samples: Cap on samples fed to the pure-Python FFT; longer
            captures are truncated to the first ``max_samples`` samples.

    Raises:
        ValueError: no/fewer than 2 numeric samples, non-numeric field,
            bad window, or no estimable sample rate.
    """
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}; expected one of {WINDOWS}")
    if max_samples < 2:
        raise ValueError("max_samples must be at least 2")

    rows: list[dict[str, Any]] = []
    for rec in query_telemetry(store, session_id, stream_id=stream_id,
                               key=key, since_ns=since_ns, until_ns=until_ns):
        v = rec["values"].get(key)
        if v is None:
            continue
        try:
            float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"field {key!r} is not numeric (got {v!r})") from exc
        rows.append(rec)

    raw_samples = len(rows)
    warnings: list[str] = []
    truncated = False
    if len(rows) > max_samples:
        rows = rows[:max_samples]
        truncated = True
        warnings.append(
            f"input longer than {max_samples} samples; truncated to the "
            f"first {max_samples} samples before transforming")

    if len(rows) < 2:
        raise ValueError(
            f"need at least 2 numeric samples for stream {stream_id} "
            f"field {key!r} in window, found {len(rows)}")

    values = [float(r["values"][key]) for r in rows]

    # Firmware-clock wraps are always counted, even when the rate itself
    # comes from elsewhere (caller override / session fallback / wall clock).
    _, clock_wraps = _source_clock_rate(rows)

    # --- sample rate, with provenance ------------------------------------
    rate_source: str
    if sample_rate_hz is not None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        fs = float(sample_rate_hz)
        rate_source = "caller"
    else:
        fs, _wraps = _source_clock_rate(rows)
        if fs is not None:
            rate_source = "source_time_ms"
        else:
            session_rate = compute_effective_rate(
                store, session_id, stream_id).get(stream_id, {}) \
                .get("effective_rate_hz")
            if session_rate:
                fs = float(session_rate)
                rate_source = "source_time_ms:session_effective_rate"
            else:
                fs = _wall_clock_rate(rows)
                if fs is None:
                    raise ValueError("could not estimate sample rate: no "
                                     "firmware-clock deltas and <2 samples")
                rate_source = "wall_clock:time_ns"
                warnings.append(
                    "firmware source_time_ms unavailable; sample rate taken "
                    "from receiver wall-clock (time_ns) median spacing")

    # --- jitter / gaps for the samples that went in ----------------------
    if since_ns is None and until_ns is None:
        jitter = compute_jitter(store, session_id, stream_id).get(
            stream_id, {"jitter_mean_ns": None, "jitter_std_ns": None,
                        "jitter_max_ns": None, "jitter_median_ns": None})
        gaps = compute_gaps(store, session_id, stream_id).get(stream_id, [])
        median_dt_ns = None
        dts_ns = [int(rows[i]["time_ns"]) - int(rows[i - 1]["time_ns"])
                  for i in range(1, len(rows))]
        if len(dts_ns) >= 2:
            median_dt_ns = statistics.median(dts_ns)
    else:
        jitter, gaps, median_dt_ns = _jitter_and_gaps(rows)

    gap_count = len(gaps)
    has_gaps = gap_count > 0
    flagged = has_gaps
    if has_gaps:
        warnings.append(
            f"{gap_count} gap(s) (> {_GAP_THRESHOLD_MULTIPLIER:g}x median "
            f"sample spacing) inside the spectrum window; bin magnitudes "
            f"are computed as if spacing were uniform and MUST be read "
            f"with caution")
    jmean = jitter.get("jitter_mean_ns")
    if median_dt_ns and jmean is not None and median_dt_ns > 0:
        ratio = jmean / median_dt_ns
        if ratio > 0.1:
            warnings.append(
                f"mean inter-sample jitter is {ratio:.1%} of median spacing; "
                f"uniform-rate assumption is rough")

    # --- transform --------------------------------------------------------
    n = len(values)
    nfft = _next_power_of_two(n)
    padded = nfft > n
    if truncated and padded:
        size_adjustment = "truncated-and-zero-padded"
    elif truncated:
        size_adjustment = "truncated"
    elif padded:
        size_adjustment = "zero-padded"
        warnings.append(
            f"{n} samples zero-padded to {nfft} for the radix-2 FFT; "
            f"frequency resolution is fs/{nfft}")
    else:
        size_adjustment = "none"

    freqs, mags, fft_size = fft_one_sided(values, fs, window)

    # Peak over non-DC bins.
    peak_idx = max(range(1, len(mags)), key=lambda k: mags[k])
    peak_freq = freqs[peak_idx]
    peak_mag = mags[peak_idx]

    return SpectrumResult(
        stream_id=stream_id,
        field=key,
        start_ns=since_ns,
        end_ns=until_ns,
        frequencies_hz=freqs,
        magnitudes=mags,
        samples_used=n,
        raw_samples=raw_samples,
        fft_size=fft_size,
        size_adjustment=size_adjustment,
        sample_rate_hz=fs,
        sample_rate_source=rate_source,
        frequency_resolution_hz=fs / fft_size,
        nyquist_hz=fs / 2.0,
        window=window,
        magnitude_convention=_MAGNITUDE_CONVENTION,
        peak_frequency_hz=peak_freq,
        peak_magnitude=peak_mag,
        jitter=jitter,
        gap_count=gap_count,
        gaps=tuple(gaps),
        clock_wrap_count=clock_wraps,
        has_gaps=has_gaps,
        flagged=flagged,
        warnings=tuple(warnings),
    )
