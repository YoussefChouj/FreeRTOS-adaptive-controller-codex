import ctypes
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis.ekf_of_model import DEFAULTS, EkfOfModel
from ground_station.analysis.ekf_of_replay import replay_arrays


class EkfOf_t(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_float * 8),
        ("P", (ctypes.c_float * 16) * 2),
        ("q_pos", ctypes.c_float),
        ("q_acc", ctypes.c_float),
        ("q_bof", ctypes.c_float),
        ("q_ba", ctypes.c_float),
        ("R_of", ctypes.c_float),
        ("R_of1", ctypes.c_float),
        ("R_zupt", ctypes.c_float),
        ("innov_x", ctypes.c_float),
        ("innov_y", ctypes.c_float),
        ("inited", ctypes.c_uint8),
        ("of_gate", ctypes.c_float),
        ("of1_gate", ctypes.c_float),
        ("rej_x", ctypes.c_uint32),
        ("rej_y", ctypes.c_uint32),
        ("rej_run_x", ctypes.c_uint8),
        ("rej_run_y", ctypes.c_uint8),
    ]

@pytest.fixture(scope="module")
def gcc_lib(tmp_path_factory):
    import tempfile
    
    gcc_path = os.environ.get("EKF_OF_GCC", shutil.which('gcc'))
    if not gcc_path:
        reason = "gcc not found"
        with open(os.path.join(tempfile.gettempdir(), "wp8_gcc_skip.txt"), "w") as f:
            f.write(reason)
        pytest.skip(reason)
        
    src = Path("API/ekf_of.c").resolve()
    tmp_dir = tmp_path_factory.mktemp("gcc_lib")
    
    ext = ".dll" if sys.platform == "win32" else ".so"
    lib_path = tmp_dir / f"libekf_of{ext}"
    
    cmd = [gcc_path, "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC"]
    if struct.calcsize("P") == 8:
        cmd.append("-m64")
    cmd.extend([str(src), "-o", str(lib_path)])
    
    try:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            # Try loading to catch architecture mismatch
            ctypes.CDLL(str(lib_path))
        except (subprocess.CalledProcessError, OSError):
            # 2026-10-07: the lab PC has only 32-bit MinGW gcc (skips every C golden on 64-bit Python) and
            # LLVM clang with no MSVC CRT; build a CRT-free DLL (_fltused shim, explicit exports) instead.
            clang = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
            if sys.platform != "win32" or not os.path.exists(clang):
                raise
            shim = tmp_dir / "fltused.c"
            shim.write_text("int _fltused = 0;\n")
            exports = [f"-Wl,-export:{n}" for n in ("EkfOf_Init", "EkfOf_Predict", "EkfOf_Update",
                       "EkfOf_UpdateRaw", "EkfOf_UpdateZeroVel", "EkfOf_ResetPos", "EkfOf_ResetBias")]
            subprocess.run([clang, "-std=c99", "-Wall", "-Wextra", "-Werror", "-fno-math-errno", "-shared",
                            "-nostdlib", "-Wl,-noentry", *exports, str(src), str(shim), "-o", str(lib_path)],
                           check=True, capture_output=True, text=True)
            ctypes.CDLL(str(lib_path))
    except (subprocess.CalledProcessError, OSError) as e:
        try:
            dump_out = subprocess.run([gcc_path, "-dumpmachine"], check=True, capture_output=True, text=True).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            dump_out = "unknown"
        reason = f"gcc {gcc_path} ({dump_out}) cannot build a library for this Python: {e}"
        with open(os.path.join(tempfile.gettempdir(), "wp8_gcc_skip.txt"), "w") as f:
            f.write(reason)
        pytest.skip(reason)
        
    return lib_path

def test_ekf_of_model_python():
    np.random.seed(42)
    dt = 0.005
    N_ground = int(5.0 / dt)
    N_flight = int(30.0 / dt)
    N = N_ground + N_flight
    
    t = np.arange(N) * dt
    true_v = np.zeros(N)
    true_v[N_ground:] = 0.5 * np.sin(2*np.pi*0.7*t[N_ground:]) * (2*np.pi*0.7)
    
    true_a = np.zeros(N)
    true_a[N_ground:] = 0.5 * np.cos(2*np.pi*0.7*t[N_ground:]) * (2*np.pi*0.7)**2
    
    bof = 0.05
    ba = 0.2
    
    a_meas = true_a + ba + np.random.normal(0, 0.2, N)
    of_meas = true_v + bof + np.random.normal(0, np.sqrt(6.16e-4), N)
    
    params = DEFAULTS.copy()
    params['q_acc'] = (0.2**2) * dt
    params['q_bof'] = 0.0  # single channel: bof learned on ZUPT, then frozen (q_bof > 0 needs of1: test_raw_channel_golden)
    
    model = EkfOfModel(1, **params)
    
    for i in range(1, N):
        model.predict(dt, a_meas[i], a_meas[i])
        if i < N_ground:
            model.update_zero_vel()
        if i % 8 == 0:
            model.update_of(of_meas[i], of_meas[i])
            
    assert np.abs(model.x[0, 2] - 0.05) < 0.005
    assert np.abs(model.x[0, 6] - 0.2) < 0.02
    
    # Check velocity rms error over flight
    v_est = []
    # run again and log
    model = EkfOfModel(1, **params)
    for i in range(1, N):
        model.predict(dt, a_meas[i], a_meas[i])
        if i < N_ground:
            model.update_zero_vel()
        if i % 8 == 0:
            model.update_of(of_meas[i], of_meas[i])
        if i >= N_ground:
            v_est.append(model.x[0, 1])
            
    v_err = np.array(v_est) - true_v[N_ground:]
    assert np.sqrt(np.mean(v_err**2)) < 0.02

def test_golden(gcc_lib):
    np.random.seed(42)
    dt = 0.005
    N_ground = int(5.0 / dt)
    N_flight = int(30.0 / dt)
    N = N_ground + N_flight
    
    t = np.arange(N) * dt
    true_v = np.zeros(N)
    true_v[N_ground:] = 0.5 * np.sin(2*np.pi*0.7*t[N_ground:]) * (2*np.pi*0.7)
    
    true_a = np.zeros(N)
    true_a[N_ground:] = 0.5 * np.cos(2*np.pi*0.7*t[N_ground:]) * (2*np.pi*0.7)**2
    
    bof = 0.05
    ba = 0.2
    
    a_meas = true_a + ba + np.random.normal(0, 0.2, N)
    of_meas = true_v + bof + np.random.normal(0, np.sqrt(6.16e-4), N)
    
    params = DEFAULTS.copy()
    params['q_acc'] = (0.2**2) * dt
    params['of_gate'] = 0.0  # ungated equivalence; gating is covered by test_gate_golden

    model = EkfOfModel(1, dtype=np.float32, **params)
    
    # C model
    lib = ctypes.CDLL(str(gcc_lib))
    lib.EkfOf_Init.argtypes = [ctypes.POINTER(EkfOf_t)]
    lib.EkfOf_Predict.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.EkfOf_Update.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float]
    lib.EkfOf_UpdateZeroVel.argtypes = [ctypes.POINTER(EkfOf_t)]
    
    c_model = EkfOf_t()
    lib.EkfOf_Init(ctypes.byref(c_model))
    
    c_model.q_pos = params['q_pos']
    c_model.q_acc = params['q_acc']
    c_model.q_bof = params['q_bof']
    c_model.q_ba = params['q_ba']
    c_model.R_of = params['R_of']
    c_model.R_zupt = params['R_zupt']
    c_model.of_gate = params['of_gate']

    for i in range(1, N):
        model.predict(dt, a_meas[i], a_meas[i])
        lib.EkfOf_Predict(ctypes.byref(c_model), dt, a_meas[i], a_meas[i])
        
        if i < N_ground:
            model.update_zero_vel()
            lib.EkfOf_UpdateZeroVel(ctypes.byref(c_model))
            
        if i % 8 == 0:
            model.update_of(of_meas[i], of_meas[i])
            lib.EkfOf_Update(ctypes.byref(c_model), of_meas[i], of_meas[i])
            
            c_x = np.array(c_model.x)
            np.testing.assert_allclose(model.x[0], c_x, rtol=1e-4, atol=1e-6)
            np.testing.assert_allclose(model.innov_x[0], c_model.innov_x, rtol=1e-4, atol=1e-6)
            np.testing.assert_allclose(model.innov_y[0], c_model.innov_y, rtol=1e-4, atol=1e-6)

def test_c_defaults(gcc_lib):
    lib = ctypes.CDLL(str(gcc_lib))
    lib.EkfOf_Init.argtypes = [ctypes.POINTER(EkfOf_t)]
    
    c_model = EkfOf_t()
    lib.EkfOf_Init(ctypes.byref(c_model))
    
    assert np.isclose(c_model.q_pos, DEFAULTS['q_pos'], rtol=1e-5)
    assert np.isclose(c_model.q_acc, DEFAULTS['q_acc'], rtol=1e-5)
    assert np.isclose(c_model.q_bof, DEFAULTS['q_bof'], rtol=1e-5)
    assert np.isclose(c_model.q_ba, DEFAULTS['q_ba'], rtol=1e-5)
    assert np.isclose(c_model.R_of, DEFAULTS['R_of'], rtol=1e-5)
    assert np.isclose(c_model.R_zupt, DEFAULTS['R_zupt'], rtol=1e-5)
    assert np.isclose(c_model.of_gate, DEFAULTS['of_gate'], rtol=1e-5)
    assert np.isclose(c_model.R_of1, DEFAULTS['R_of1'], rtol=1e-5)
    assert np.isclose(c_model.of1_gate, DEFAULTS['of1_gate'], rtol=1e-5)
    assert c_model.rej_x == 0 and c_model.rej_y == 0
    
    py_model = EkfOfModel(1, dtype=np.float32)
    c_P = np.array(c_model.P).reshape(2, 4, 4)
    np.testing.assert_allclose(py_model.P[0], c_P, rtol=1e-5)

def test_gate_golden(gcc_lib):
    """Default 5-sigma gate: C and Python skip the same outliers and stay equal."""
    rng = np.random.default_rng(7)
    dt = 0.005
    N = int(20.0 / dt)
    of_meas = 0.02 + rng.normal(0, 0.01, N)
    outliers = set(range(800, N, 400))  # every 2 s, on OF-update ticks (multiples of 8)
    for i in outliers:
        of_meas[i] += 0.5
    model = EkfOfModel(1, dtype=np.float32)
    lib = ctypes.CDLL(str(gcc_lib))
    lib.EkfOf_Init.argtypes = [ctypes.POINTER(EkfOf_t)]
    lib.EkfOf_Predict.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.EkfOf_Update.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float]
    c_model = EkfOf_t()
    lib.EkfOf_Init(ctypes.byref(c_model))
    for i in range(1, N):
        model.predict(dt, 0.0, 0.0)
        lib.EkfOf_Predict(ctypes.byref(c_model), dt, 0.0, 0.0)
        if i % 8 == 0:
            x_before = c_model.x[1]
            model.update_of(of_meas[i], of_meas[i])
            lib.EkfOf_Update(ctypes.byref(c_model), of_meas[i], of_meas[i])
            if i in outliers:
                assert c_model.x[1] == x_before  # rejected: velocity untouched
            np.testing.assert_allclose(model.x[0], np.array(c_model.x), rtol=1e-4, atol=1e-6)
    assert c_model.rej_x == model.rej_x[0] == c_model.rej_y == model.rej_y[0]
    assert c_model.rej_x >= len(outliers)


def test_gate_lockout_release_golden(gcc_lib):
    """Long ZUPT then a +0.12 m/s step (x_y_calibration_hitting_wall_1 lift-off): the 5-sigma
    gate rejects it, and after REJ_RELEASE frames C and Python both re-acquire it identically."""
    from ground_station.analysis.ekf_of_model import REJ_RELEASE
    dt = 0.005
    model = EkfOfModel(1, dtype=np.float32)
    lib = ctypes.CDLL(str(gcc_lib))
    lib.EkfOf_Init.argtypes = [ctypes.POINTER(EkfOf_t)]
    lib.EkfOf_Predict.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.EkfOf_Update.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float]
    lib.EkfOf_UpdateZeroVel.argtypes = [ctypes.POINTER(EkfOf_t)]
    lib.EkfOf_ResetBias.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float]
    c_model = EkfOf_t()
    lib.EkfOf_Init(ctypes.byref(c_model))
    for _ in range(2000):  # 10 s on the ground
        model.predict(dt, 0.0, 0.0)
        lib.EkfOf_Predict(ctypes.byref(c_model), dt, 0.0, 0.0)
        model.update_zero_vel()
        lib.EkfOf_UpdateZeroVel(ctypes.byref(c_model))
    model.reset_bias(0.0)  # ARM: EKF_OF_BOF_ARM_VAR
    lib.EkfOf_ResetBias(ctypes.byref(c_model), 0.0)
    frames = []
    for i in range(1, 20 * 8 + 1):  # 0.8 s airborne, OF frame every 8 ticks
        model.predict(dt, 0.0, 0.0)
        lib.EkfOf_Predict(ctypes.byref(c_model), dt, 0.0, 0.0)
        if i % 8 == 0:
            model.update_of(0.12, -0.12)
            lib.EkfOf_Update(ctypes.byref(c_model), 0.12, -0.12)
            frames.append((c_model.x[1], c_model.x[4], c_model.rej_x))
            np.testing.assert_allclose(model.x[0], np.array(c_model.x), rtol=1e-4, atol=1e-6)
    assert frames[0][2] == 1                        # the step is gated at first
    assert c_model.rej_x == REJ_RELEASE == c_model.rej_y  # released once, then tracked
    assert abs(frames[REJ_RELEASE - 1][0] - 0.12) < 0.01 and abs(frames[REJ_RELEASE - 1][1] + 0.12) < 0.01
    assert abs(c_model.innov_x) < 0.061 and abs(c_model.innov_y) < 0.061  # under EKF_OF_HEALTH_THRESH


def _run_two_channel(model, lib, c_model, of2, of1, ticks, dt=0.005):
    for i in range(1, ticks):
        model.predict(dt, 0.0, 0.0)
        lib.EkfOf_Predict(ctypes.byref(c_model), dt, 0.0, 0.0)
        if i % 8 == 0:
            model.update_of(of2[i], -of2[i])
            lib.EkfOf_Update(ctypes.byref(c_model), of2[i], -of2[i])
            model.update_raw(of1[i], -of1[i])
            lib.EkfOf_UpdateRaw(ctypes.byref(c_model), of1[i], -of1[i])
            np.testing.assert_allclose(model.x[0], np.array(c_model.x), rtol=1e-4, atol=1e-6)


def _two_channel_lib(gcc_lib):
    lib = ctypes.CDLL(str(gcc_lib))
    lib.EkfOf_Init.argtypes = [ctypes.POINTER(EkfOf_t)]
    lib.EkfOf_Predict.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.EkfOf_Update.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float]
    lib.EkfOf_UpdateRaw.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float, ctypes.c_float]
    lib.EkfOf_ResetBias.argtypes = [ctypes.POINTER(EkfOf_t), ctypes.c_float]
    return lib


def test_raw_channel_golden(gcc_lib):
    """Defaults (q_bof 1e-5, R_of1 1e-3): of2 = v + 0.05, of1 = v. C and Python stay equal, bof learns
    the of2 bias (the 2026-10-07 fix), and a 0.5 m/s of1 glitch is gated without touching the state."""
    rng = np.random.default_rng(11)
    N = int(30.0 / 0.005)
    v = 0.1 * np.sin(np.arange(N) * 0.005)
    of2 = v + 0.05 + rng.normal(0, 0.01, N)
    of1 = v + rng.normal(0, 0.02, N)
    of1[2400] += 0.5   # an OF-update tick (multiple of 8)
    model = EkfOfModel(1, dtype=np.float32)
    lib = _two_channel_lib(gcc_lib)
    c_model = EkfOf_t()
    lib.EkfOf_Init(ctypes.byref(c_model))
    model.reset_bias(0.0)
    lib.EkfOf_ResetBias(ctypes.byref(c_model), 0.0)
    _run_two_channel(model, lib, c_model, of2, of1, 2400)
    x_before = np.array(c_model.x)
    lib.EkfOf_UpdateRaw(ctypes.byref(c_model), of1[2400], -of1[2400])
    np.testing.assert_array_equal(np.array(c_model.x), x_before)  # gated
    lib.EkfOf_Init(ctypes.byref(c_model))
    model = EkfOfModel(1, dtype=np.float32)
    _run_two_channel(model, lib, c_model, of2, of1, N)
    assert abs(c_model.x[2] - 0.05) < 0.01 and abs(c_model.x[5] + 0.05) < 0.01


def test_raw_channel_off(gcc_lib):
    """R_of1 0 = channel off: EkfOf_UpdateRaw and update_raw leave the state untouched."""
    model = EkfOfModel(1, dtype=np.float32, R_of1=0.0)
    lib = _two_channel_lib(gcc_lib)
    c_model = EkfOf_t()
    lib.EkfOf_Init(ctypes.byref(c_model))
    c_model.R_of1 = 0.0
    x0, P0 = model.x.copy(), model.P.copy()
    model.update_raw(0.3, 0.3)
    lib.EkfOf_UpdateRaw(ctypes.byref(c_model), 0.3, 0.3)
    np.testing.assert_array_equal(model.x, x0)
    np.testing.assert_array_equal(model.P, P0)
    assert list(c_model.x) == [0.0] * 8


def test_replay_arrays():
    N1 = 1000
    t1 = np.arange(N1) * 0.02
    phase1 = np.zeros(N1, dtype=int)
    phase1[200:800] = 1
    phase1[800:] = 3
    ax1 = np.zeros(N1)
    ay1 = np.zeros(N1)
    
    N3 = 500
    t3 = np.arange(N3) * 0.04
    ofx3 = np.zeros(N3)
    ofy3 = np.zeros(N3)
    ofq3 = np.full(N3, 255)
    
    params_grid = [DEFAULTS.copy(), DEFAULTS.copy()]
    params_grid[1]['q_acc'] = 0.1
    
    res = replay_arrays(t1, phase1, ax1, ay1, t3, ofx3, ofy3, ofq3, params_grid, run_old=True)
    assert len(res) == 2
    for r in res:
        assert np.isfinite(r['nis'])
        assert np.isfinite(r['autocorr'])
        assert np.isfinite(r['bof_drift'])

TILT_GAIN = 0.242  # EKF_OF_TILT_GAIN
G = 9.80665


def _tilt_scene(dt=0.005, T=4.0, k_true=TILT_GAIN):
    """Body tilt oscillating +-0.1 rad at 0.5 Hz; the true velocity is driven only by k_true * g * tilt."""
    t = np.arange(int(T / dt)) * dt
    tilt = 0.1 * np.sin(2 * np.pi * 0.5 * t)
    a_true = k_true * G * tilt
    v_true = np.cumsum(a_true) * dt
    return t, tilt, v_true


def _run_tilt_filter(sign, dt=0.005, of_stop=1.0):
    """Tilt input (gain TILT_GAIN, given sign) into the twin; OF at 25 Hz only until `of_stop` s, then coast."""
    t, tilt, v_true = _tilt_scene(dt)
    m = EkfOfModel(1, **{k: DEFAULTS[k] for k in DEFAULTS})
    v_est = np.zeros(len(t))
    for i in range(len(t)):
        m.predict(dt, sign * TILT_GAIN * G * tilt[i], 0.0)
        if t[i] < of_stop and i % 8 == 0:
            m.update_of(v_true[i], 0.0)
        v_est[i] = m.x[0, 1]
    coast = t >= of_stop
    return t, v_true, v_est, coast


def test_tilt_input_tracks_velocity():
    # With OF removed after 1 s the velocity estimate is driven by the tilt term alone.
    # Tolerance: coasting rms error < 0.01 m/s on a +-0.07 m/s signal (the signal rms is ~0.05 m/s).
    t, v_true, v_est, coast = _run_tilt_filter(+1.0)
    rms_err = np.sqrt(np.mean((v_est[coast] - v_true[coast]) ** 2))
    rms_sig = np.sqrt(np.mean(v_true[coast] ** 2))
    assert rms_sig > 0.03
    assert rms_err < 0.01, f"coast rms error {rms_err:.4f} m/s"


def test_tilt_input_wrong_sign_fails():
    # Same scene with the sign flipped: the coasting estimate moves against the true velocity.
    t, v_true, v_est, coast = _run_tilt_filter(-1.0)
    rms_err = np.sqrt(np.mean((v_est[coast] - v_true[coast]) ** 2))
    assert rms_err > 0.03, f"coast rms error {rms_err:.4f} m/s (wrong sign must not track)"
    assert np.corrcoef(v_est[coast], v_true[coast])[0, 1] < 0.0


def test_fit_tilt_gain_recovers_k_and_sign():
    from ground_station.analysis.ekf_of_replay import fit_tilt_gain
    dt = 0.005
    t, tilt, v_true = _tilt_scene(dt, T=12.0, k_true=0.25)
    phase = np.where(t < 1.0, 0, 1)
    ax1 = G * tilt            # sign +1, gain 1
    t3 = t[::8]
    of = v_true[::8]
    q = np.full(len(t3), 255)
    k_x, k_y, n = fit_tilt_gain(t, phase, ax1, -ax1, t3, of, of, q)
    assert n > 10
    assert abs(k_x - 0.25) < 0.03, k_x       # right sign: k > 0 and close to the true gain
    assert abs(k_y + 0.25) < 0.03, k_y       # flipped sign: k < 0


def test_boot_layout_contains_states():
    with open('ground_station/comm/boot_default_layout.py', 'r') as f:
        content = f.read()
    assert '"s_ekf_of.x[6]"' in content
    assert '"s_ekf_of.x[7]"' in content
    assert '"s_ekf_of.innov_x"' in content
    assert '"s_ekf_of.innov_y"' in content
    assert '"g_ekf_of_health"' in content

def test_replay_real_logs(capsys):
    import glob
    import tempfile

    from ground_station.analysis.ekf_of_replay import resolve_logs_dir, run_cli
    
    # resolve_logs_dir honours $EKF_OF_LOGS_DIR, then <repo>/logs/vofa, the VPS path, the git-common-dir sibling.
    logs_dir = resolve_logs_dir()
    if not logs_dir or not logs_dir.exists():
        pytest.skip("Logs dir not found (set EKF_OF_LOGS_DIR)")
        
    import sys
    orig_argv = sys.argv
    sys.argv = ['ekf_of_replay.py']
    try:
        run_cli()
    except SystemExit as e:
        if e.code != 0:
            raise RuntimeError(f"run_cli exited with {e.code}")
    finally:
        sys.argv = orig_argv
        
    captured = capsys.readouterr()
    out = captured.out
    
    with open(os.path.join(tempfile.gettempdir(), "wp8_replay_last.txt"), "w") as f:
        f.write(out)
        f.write(captured.err)
    
    assert out.count("--- Log: ") == 5, "Expected 5 per-log tables"
    # whole words only: the "Informational RMS" line would match a bare ' inf' substring
    import re
    assert not re.search(r'\b(nan|inf)\b', out.lower()), "Metrics must be finite"
    
    if "DEFAULTS_MATCH yes" not in out:
        idx = out.find("Top 5 combos:")
        tuning_result = out[idx:] if idx != -1 else out
        assert False, f"DEFAULTS_MATCH yes not found. Tuning result:\n{tuning_result}"

def test_fake_log_loader(tmp_path):
    import json
    import shutil
    import tempfile

    from ground_station.analysis.ekf_of_replay import load_vofa
    
    stem = "fake_log"
    meta = {
        "preset": {
            "slots": [
                {"path": "slot0"},
                {"path": "slot1"},
                {"path": "slot2"},
                {"path": "slot3"}
            ]
        }
    }
    
    meta_path = tmp_path / f"{stem}.meta.json"
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f)
        
    for i in range(4):
        csv_path = tmp_path / f"{stem}.slot{i}.csv"
        with open(csv_path, 'w', encoding='utf-8') as f:
            if i == 0:
                f.write("t_src_ms,t_host_s,seq\n")
            elif i == 1:
                f.write("t_src_ms,t_host_s,seq,Acc_X_Real,Acc_Y_Real,flight_phase\n")
                f.write("1000,1.0,1,0.1,0.2,1\n")
                f.write("1005,1.005,2,0.11,0.21,1\n")
            elif i == 2:
                f.write("t_src_ms,t_host_s,seq,Ctrler.rollPID.FB,Ctrler.pitchPID.FB\n")
                f.write("1000,1.0,1,0.5,0.6\n")
            elif i == 3:
                f.write("t_src_ms,t_host_s,seq,ano_of.of2_dx_fix,ano_of.of2_dy_fix,s_of_bias_x,s_of_bias_y,ano_of.of_quality,s_ekf_of.x[0],s_ekf_of.x[3]\n")
                f.write("1000,1.0,1,10,20,1.0,2.0,200,0.5,0.6\n")
                
    # Use the same fallback logic from ekf_of_replay.py
    try:
        from ground_station.analysis.flightlab.loaders import LoadError
        L = load_vofa(meta_path)
    except LoadError:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpp = Path(tmpdir)
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            slots = meta['preset']['slots']
            new_slots = [s for s in slots if 'slot1' in s.get('path','') or 'slot2' in s.get('path','') or 'slot3' in s.get('path','')]
            meta['preset']['slots'] = new_slots
            
            tmp_meta = tmpp / f"{stem}.meta.json"
            with open(tmp_meta, 'w', encoding='utf-8') as f:
                json.dump(meta, f)
                
            for i in range(1, 4):
                orig_csv = tmp_path / f"{stem}.slot{i}.csv"
                if orig_csv.exists():
                    shutil.copyfile(orig_csv, tmpp / f"{stem}.slot{i-1}.csv")
            L = load_vofa(tmp_meta)
            
    assert 'Acc_X_Real' in L.signals
    assert len(L.signals['Acc_X_Real'].v) == 2
