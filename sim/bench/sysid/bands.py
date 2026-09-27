"""Zero-phase FFT band split with smooth cosine transitions.

Pre-registered bands (PREREG2.md section 1):
  - L: below 0.5 Hz
  - M: 0.5 - 4.0 Hz
  - H: above 4.0 Hz

A smooth cosine transition of 0.1 decade is used at each cutoff.
Reflect padding is applied to eliminate edge transients.
L + M + H reconstructs the input signal to machine precision (< 1e-12).
"""
import numpy as np


class BandSplit(dict):
    """Dictionary holding {'L': x_L, 'M': x_M, 'H': x_H}, iterable as (L, M, H)."""
    def __iter__(self):
        return iter((self['L'], self['M'], self['H']))


def split_bands(x, dt=0.005, f_cuts=(0.5, 4.0), trans_decade=0.1, axis=0):
    """Split signal x into L, M, H bands along `axis` using zero-phase FFT filtering.

    Parameters:
        x: ndarray of signal(s)
        dt: sampling interval [s] (default 0.005 s = 200 Hz)
        f_cuts: tuple of (f_low, f_high) cutoff frequencies in Hz (default (0.5, 4.0))
        trans_decade: transition width in decades (default 0.1)
        axis: axis along which time series is oriented

    Returns:
        BandSplit dict with keys 'L', 'M', 'H', also unpackable as (L, M, H).
    """
    x = np.asarray(x)
    n_samples = x.shape[axis]
    if n_samples <= 2:
        return BandSplit(L=x.copy(), M=np.zeros_like(x), H=np.zeros_like(x))

    # Move target axis to front for uniform 1D/2D/ND processing
    if axis != 0:
        x_proc = np.moveaxis(x, axis, 0)
    else:
        x_proc = x

    orig_shape = x_proc.shape
    flattened_cols = int(np.prod(orig_shape[1:])) if len(orig_shape) > 1 else 1
    x_2d = x_proc.reshape(n_samples, flattened_cols)

    # Reflect padding: at least 2 periods of lowest cutoff frequency
    pad_len = min(n_samples - 1, int(2.0 / (f_cuts[0] * dt)))
    x_padded = np.pad(x_2d, ((pad_len, pad_len), (0, 0)), mode='reflect')
    n_padded = x_padded.shape[0]

    # Real FFT
    X_fft = np.fft.rfft(x_padded, axis=0)
    freqs = np.fft.rfftfreq(n_padded, d=dt)

    def transition_filter(fc):
        """Cosine transition lowpass filter centered at fc with width trans_decade."""
        delta = trans_decade / 2.0
        f_lo = fc * (10.0 ** (-delta))
        f_hi = fc * (10.0 ** delta)
        with np.errstate(divide='ignore'):
            log_f = np.where(freqs > 0, np.log10(freqs), -np.inf)
        u = np.clip((log_f - np.log10(f_lo)) / (np.log10(f_hi) - np.log10(f_lo)), 0.0, 1.0)
        # Cosine smooth step: 1.0 below f_lo, 0.0 above f_hi, 0.5*(1+cos(pi*u)) in between
        return 0.5 * (1.0 + np.cos(np.pi * u))

    T1 = transition_filter(f_cuts[0])[:, None]
    T2 = transition_filter(f_cuts[1])[:, None]

    WL = T1
    WM = T2 - T1
    WH = 1.0 - T2

    # Inverse FFT and unpad
    x_L = np.fft.irfft(X_fft * WL, n=n_padded, axis=0)[pad_len:pad_len + n_samples]
    x_M = np.fft.irfft(X_fft * WM, n=n_padded, axis=0)[pad_len:pad_len + n_samples]
    x_H = np.fft.irfft(X_fft * WH, n=n_padded, axis=0)[pad_len:pad_len + n_samples]

    x_L = x_L.reshape(orig_shape)
    x_M = x_M.reshape(orig_shape)
    x_H = x_H.reshape(orig_shape)

    if axis != 0:
        x_L = np.moveaxis(x_L, 0, axis)
        x_M = np.moveaxis(x_M, 0, axis)
        x_H = np.moveaxis(x_H, 0, axis)

    return BandSplit(L=x_L, M=x_M, H=x_H)
