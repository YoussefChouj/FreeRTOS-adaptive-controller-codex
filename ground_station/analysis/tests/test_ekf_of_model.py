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
        ("R_zupt", ctypes.c_float),
        ("innov_x", ctypes.c_float),
        ("innov_y", ctypes.c_float),
        ("inited", ctypes.c_uint8)
    ]

@pytest.fixture(scope="module")
def gcc_lib(tmp_path_factory):
    gcc_path = os.environ.get("EKF_OF_GCC", shutil.which('gcc'))
    if not gcc_path:
        pytest.skip("gcc not found")
        
    src = Path("API/ekf_of.c").resolve()
    tmp_dir = tmp_path_factory.mktemp("gcc_lib")
    
    ext = ".dll" if sys.platform == "win32" else ".so"
    lib_path = tmp_dir / f"libekf_of{ext}"
    
    cmd = [gcc_path, "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC"]
    if struct.calcsize("P") == 8:
        cmd.append("-m64")
    cmd.extend([str(src), "-o", str(lib_path)])
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        # Try loading to catch architecture mismatch
        ctypes.CDLL(str(lib_path))
    except (subprocess.CalledProcessError, OSError) as e:
        try:
            dump_out = subprocess.run([gcc_path, "-dumpmachine"], check=True, capture_output=True, text=True).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            dump_out = "unknown"
        pytest.skip(f"gcc {gcc_path} ({dump_out}) cannot build a library for this Python: {e}")
        
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
    
    py_model = EkfOfModel(1, dtype=np.float32)
    c_P = np.array(c_model.P).reshape(2, 4, 4)
    np.testing.assert_allclose(py_model.P[0], c_P, rtol=1e-5)

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

def test_boot_layout_contains_states():
    with open('ground_station/comm/boot_default_layout.py', 'r') as f:
        content = f.read()
    assert '"s_ekf_of.x[6]"' in content
    assert '"s_ekf_of.x[7]"' in content

def test_replay_real_logs(capsys):
    import glob

    from ground_station.analysis.ekf_of_replay import resolve_logs_dir, run_cli
    
    logs_dir = resolve_logs_dir()
    if not logs_dir or not logs_dir.exists():
        pytest.skip("Logs dir not found")
        
    log_files = glob.glob(str(logs_dir / 'f17_hover_*.meta.json'))
    if len(log_files) < 5:
        pytest.skip(f"Found {len(log_files)} logs, need 5")
        
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
    
    assert out.count("--- Log: ") == 5, "Expected 5 per-log tables"
    assert ' nan' not in out.lower() and ' inf' not in out.lower(), "Metrics must be finite"
    
    if "DEFAULTS_MATCH yes" not in out:
        idx = out.find("Top 5 combos:")
        tuning_result = out[idx:] if idx != -1 else out
        assert False, f"DEFAULTS_MATCH yes not found. Tuning result:\n{tuning_result}"
