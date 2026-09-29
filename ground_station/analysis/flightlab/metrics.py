import numpy as np

def rms(x):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) == 0: return float('nan')
    return float(np.sqrt(np.mean(x**2)))

def mean(x):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) == 0: return float('nan')
    return float(np.mean(x))

def std(x):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) == 0: return float('nan')
    return float(np.std(x, ddof=0))

def p95_abs(x):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) == 0: return float('nan')
    return float(np.percentile(np.abs(x), 95))

def max_abs(x):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) == 0: return float('nan')
    return float(np.max(np.abs(x)))

def frac_true(mask):
    """HEURISTIC DEFAULTS"""
    mask = np.asarray(mask)
    if len(mask) == 0: return float('nan')
    return float(np.sum(mask) / len(mask))

def iae(t, e):
    """HEURISTIC DEFAULTS"""
    t = np.asarray(t)
    e = np.asarray(e)
    valid = np.isfinite(e)
    t = t[valid]
    e = e[valid]
    if len(t) < 2: return float('nan')
    return float(np.trapezoid(np.abs(e), t))

def itae(t, e):
    """HEURISTIC DEFAULTS"""
    t = np.asarray(t)
    e = np.asarray(e)
    valid = np.isfinite(e)
    t = t[valid]
    e = e[valid]
    if len(t) < 2: return float('nan')
    return float(np.trapezoid((t - t[0]) * np.abs(e), t))

def welch_psd(x, fs, nperseg):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    n = len(x)
    
    if n < 2:
        return np.array([]), np.array([])
        
    nperseg = min(nperseg, n)
    step = nperseg - nperseg // 2
    
    w = np.hanning(nperseg + 1)[:-1]
    w2_sum = np.sum(w**2)
    
    starts = range(0, n - nperseg + 1, step)
    P_sum = np.zeros(nperseg // 2 + 1)
    
    for start in starts:
        seg = x[start:start+nperseg]
        seg = seg - np.mean(seg)
        X = np.fft.rfft(w * seg)
        P_sum += np.abs(X)**2 / (fs * w2_sum)
        
    P = P_sum / len(starts)
    
    if nperseg % 2 == 0:
        P[1:-1] *= 2
    else:
        P[1:] *= 2
        
    f = np.fft.rfftfreq(nperseg, 1/fs)
    return f, P

def psd_peaks(f, p, fmin, fmax, k):
    """HEURISTIC DEFAULTS"""
    peaks = []
    for i in range(1, len(p) - 1):
        if p[i] > p[i-1] and p[i] >= p[i+1]:
            if f[i] >= fmin and (fmax is None or f[i] <= fmax):
                peaks.append({"hz": float(f[i]), "psd": float(p[i])})
    
    peaks.sort(key=lambda item: item["psd"], reverse=True)
    return peaks[:k]

def band_power(f, p, lo, hi):
    """HEURISTIC DEFAULTS"""
    f = np.asarray(f)
    p = np.asarray(p)
    mask = (f >= lo)
    if hi is not None:
        mask &= (f < hi)
    
    f_band = f[mask]
    p_band = p[mask]
    
    if len(f_band) < 2:
        return float('nan')
    return float(np.trapezoid(p_band, f_band))

def xcorr_lag_s(a, b, fs, max_lag_s):
    """HEURISTIC DEFAULTS"""
    a = np.asarray(a)
    b = np.asarray(b)
    valid = np.isfinite(a) & np.isfinite(b)
    a = a[valid]
    b = b[valid]
    n = len(a)
    
    if n < 2 or np.std(a) == 0 or np.std(b) == 0:
        return float('nan')
        
    a = a - np.mean(a)
    b = b - np.mean(b)
    
    M = min(round(max_lag_s * fs), n - 1)
    
    lags = range(-M, M + 1)
    c = np.zeros(len(lags))
    
    for i, L in enumerate(lags):
        if L > 0:
            a_shift = a[:-L]
            b_shift = b[L:]
        elif L < 0:
            a_shift = a[-L:]
            b_shift = b[:L]
        else:
            a_shift = a
            b_shift = b
            
        c[i] = np.mean(a_shift * b_shift)
        
    best_L = lags[np.argmax(c)]
    return float(best_L / fs)

def gain_phase_at(des, fb, fs, f0):
    """HEURISTIC DEFAULTS"""
    des = np.asarray(des)
    fb = np.asarray(fb)
    valid = np.isfinite(des) & np.isfinite(fb)
    des = des[valid]
    fb = fb[valid]
    n = len(des)
    
    if n < 2:
        return float('nan'), float('nan')
        
    nperseg = min(n, round(fs * 4 / f0))
    j = round(f0 * nperseg / fs)
    
    if j > nperseg // 2:
        return float('nan'), float('nan')
        
    step = nperseg - nperseg // 2
    w = np.hanning(nperseg + 1)[:-1]
    
    starts = range(0, n - nperseg + 1, step)
    
    sum_conjXY = 0j
    sum_magX2 = 0.0
    
    for start in starts:
        des_seg = des[start:start+nperseg]
        fb_seg = fb[start:start+nperseg]
        
        des_seg = des_seg - np.mean(des_seg)
        fb_seg = fb_seg - np.mean(fb_seg)
        
        X = np.fft.rfft(w * des_seg)
        Y = np.fft.rfft(w * fb_seg)
        
        Xj = X[j]
        Yj = Y[j]
        
        sum_conjXY += np.conj(Xj) * Yj
        sum_magX2 += np.abs(Xj)**2
        
    if sum_magX2 == 0:
        return float('nan'), float('nan')
        
    H = sum_conjXY / sum_magX2
    gain = float(np.abs(H))
    phase_deg = float(np.degrees(np.angle(H)))
    
    return gain, phase_deg

def linear_slope(t, y):
    """HEURISTIC DEFAULTS"""
    t = np.asarray(t)
    y = np.asarray(y)
    valid = np.isfinite(y)
    t = t[valid]
    y = y[valid]
    
    if len(t) < 2:
        return float('nan')
        
    return float(np.polyfit(t, y, 1)[0])

def settle_time(t, y, frac=0.1):
    """HEURISTIC DEFAULTS"""
    t = np.asarray(t)
    y = np.asarray(y)
    valid = np.isfinite(y)
    t = t[valid]
    y = y[valid]
    
    n = len(y)
    if n == 0:
        return float('nan')
        
    n_final = max(1, n // 10)
    y_final = np.mean(y[-n_final:])
    
    band = frac * np.abs(y_final - y[0])
    if band == 0:
        return 0.0
        
    diff = np.abs(y - y_final)
    within_band = diff <= band
    
    for k in range(n):
        if np.all(within_band[k:]):
            return float(t[k] - t[0])
            
    return float('nan')

def wrap_deg(x):
    """HEURISTIC DEFAULTS"""
    return ((x + 180) % 360) - 180

def lowpass_1pole(x, fs, fc):
    """HEURISTIC DEFAULTS"""
    x = np.asarray(x)
    n = len(x)
    if n == 0:
        return np.array([])
        
    a = 1 - np.exp(-2 * np.pi * fc / fs)
    y = np.zeros(n)
    
    y[0] = x[0]
    for k in range(1, n):
        if np.isfinite(x[k]):
            y[k] = y[k-1] + a * (x[k] - y[k-1])
        else:
            y[k] = y[k-1]
            
    return y

def segment_mask(t, intervals):
    """HEURISTIC DEFAULTS"""
    t = np.asarray(t)
    mask = np.zeros(len(t), dtype=bool)
    if not intervals:
        return mask
    for t0, t1 in intervals:
        mask |= (t >= t0) & (t < t1)
    return mask

def longest_interval(intervals):
    """HEURISTIC DEFAULTS"""
    if not intervals:
        return None
    
    best_interval = intervals[0]
    best_len = best_interval[1] - best_interval[0]
    
    for t0, t1 in intervals[1:]:
        length = t1 - t0
        if length > best_len:
            best_len = length
            best_interval = (t0, t1)
            
    return best_interval
