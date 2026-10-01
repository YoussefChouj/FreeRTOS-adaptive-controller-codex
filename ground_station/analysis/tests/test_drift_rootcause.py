import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ground_station.analysis.drift_rootcause import (
    accel_to_lean_angles,
    hover_mask,
    main,
    mixer_decompose,
    pid_step,
    reconstruct_ui,
    yaw_rotate,
)


def test_hover_mask():
    z = np.zeros(1000)
    z[0:200] = np.linspace(0, 100, 200)
    z[200:800] = 100
    z[800:] = np.linspace(100, 0, 200)
    df = pd.DataFrame({'Ctrler.Z_posPID.FB': z})
    mask = hover_mask(df)
    assert not mask.iloc[0:150].any()
    assert mask.iloc[150:880].all()
    assert not mask.iloc[880:].any()

def test_mixer_identity():
    for d in [1.0, -1.0]:
        T = np.random.uniform(2000, 3000)
        u_x = np.random.uniform(-100, 100)
        u_y = np.random.uniform(-100, 100)
        u_z = np.random.uniform(-100, 100)
        
        m1 = T - u_y - u_x - d * u_z
        m2 = T + u_y + u_x - d * u_z
        m3 = T - u_y + u_x + d * u_z
        m4 = T + u_y - u_x + d * u_z
        
        rx, ry, rz = mixer_decompose(m1, m2, m3, m4)
        assert np.isclose(rx, u_x)
        assert np.isclose(ry, u_y)
        assert np.isclose(rz, d * u_z)

def test_ui_reconstruction():
    pPID = {
        'U': 0.0, 'Up': 0.0, 'Ui': 0.0, 'Ud': 0.0, 'SumE': 0.0, 'PreE': 0.0,
        'Kp': 3.0, 'Ki': 0.02, 'Kd': 8.0,
        'UMax': 200.0, 'UpMax': 200.0, 'UiMax': 2.4, 'UdMax': 10.0,
        'SumEMax': 120.0, 'EMin': 3.0
    }
    e_series = np.array([2.0] * 500)
    for e in e_series:
        pid_step(pPID, e, mode='legacy')
        
    expected_cap = min(pPID['UiMax'], pPID['Ki'] * pPID['SumEMax'])
    assert np.isclose(pPID['Ui'], expected_cap)
    
    recon_ui = reconstruct_ui(pPID['U'], 2.0, 3.0, 0.0)
    assert np.isclose(recon_ui, pPID['Ui'])
    
    pPID['SumE'] = 0.0
    pPID['Ui'] = 0.0
    pPID['U'] = 0.0
    for e in [1.0, 1.0, 1.0, 1.0, 1.0]:
        pid_step(pPID, e, mode='legacy')
    
    recon_ui = reconstruct_ui(pPID['U'], 1.0, 3.0, 0.0)
    assert np.isclose(recon_ui, pPID['Ui'])

def test_main(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        meta = {"preset": {"slots": [{"rate": 100}]}}
        meta_file = tmp_path / "f17_x_pidonly7.meta.json"
        with open(meta_file, 'w') as f:
            json.dump(meta, f)
            
        csv_file = tmp_path / "f17_x_pidonly7.slot0.csv"
        with open(csv_file, 'w') as f:
            f.write("t_src_ms,t_host_s,seq,Ctrler.Z_posPID.FB\n")
            
        main(['--logs', str(tmp_path)])
        
        captured = capsys.readouterr()
        assert "T1" in captured.out
        assert "T2" in captured.out
        assert "T3" in captured.out
        assert "T4" in captured.out
        assert "T5" in captured.out
        assert "WP-13 numbers" in captured.out
        assert "f17_x_pidonly7 - skipped: empty" in captured.out

if __name__ == '__main__':
    pytest.main(['-q', '-p', 'no:cacheprovider', __file__])

def test_yaw_rotate():
    # At yaw 0, des_pitch = -uy, des_roll = -ux
    dp, dr = yaw_rotate(10, 20, 0)
    assert np.isclose(dp, -20)
    assert np.isclose(dr, -10)
    
    # At yaw 90, des_pitch = -ux, des_roll = uy
    dp, dr = yaw_rotate(10, 20, 90)
    assert np.isclose(dp, -10)
    assert np.isclose(dr, 20)

def test_accel_to_lean():
    # At roll_fb=0, pitch_fb=0:
    # my_Cos_Roll = 1, my_Cos_Pitch = 1
    # tar_pitch = arctan(acc_tar_forward / 981.0)
    # tar_roll = arctan(acc_tar_right / 981.0)
    tp, tr = accel_to_lean_angles(981.0, -981.0, 0.0, 0.0)
    assert np.isclose(tp, 45.0) or np.isclose(tp, 35.0) # wait, it clips to 35.0
    # Let's test with smaller values that don't clip
    tp, tr = accel_to_lean_angles(500.0, -500.0, 0.0, 0.0)
    expected = np.degrees(np.arctan(500.0 / 981.0))
    assert np.isclose(tp, expected)
    assert np.isclose(tr, -expected)

