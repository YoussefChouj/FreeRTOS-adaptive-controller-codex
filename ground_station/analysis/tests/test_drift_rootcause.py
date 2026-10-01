import pytest
import numpy as np
import pandas as pd
import tempfile
import json
from pathlib import Path
from ground_station.analysis.drift_rootcause import hover_mask, mixer_decompose, pid_step, reconstruct_ui, main

def test_hover_mask():
    # synthetic altitude ramp/hold/land
    t = np.arange(1000)
    z = np.zeros(1000)
    # 0 to 200: ramp to 100
    z[0:200] = np.linspace(0, 100, 200)
    # 200 to 800: hold at 100
    z[200:800] = 100
    # 800 to 1000: land to 0
    z[800:] = np.linspace(100, 0, 200)
    
    df = pd.DataFrame({'Ctrler.Z_posPID.FB': z})
    mask = hover_mask(df)
    
    # p95 is ~100. 0.6 * p95 = 60.
    # > 60 is from idx 120 to 880.
    # first 15% skipped: 15% of 1000 is 150.
    # so mask should be True from 150 to 880.
    assert not mask.iloc[0:150].any()
    assert mask.iloc[150:880].all()
    assert not mask.iloc[880:].any()

def test_mixer_identity():
    # build M1..M4 from random T, u_x, u_y, u_z, d=+/-1
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
    
    # Run pid_step until Ui settles at the cap
    # Error < EMin to accumulate
    e_series = np.array([2.0] * 500)
    for e in e_series:
        pid_step(pPID, e)
        
    expected_cap = min(pPID['UiMax'], pPID['Ki'] * pPID['SumEMax'])
    assert np.isclose(pPID['Ui'], expected_cap)
    
    recon_ui = reconstruct_ui(pPID['U'], 2.0, 3.0)
    assert np.isclose(recon_ui, pPID['Ui'])
    
    # Test below cap
    pPID['SumE'] = 0.0
    pPID['Ui'] = 0.0
    pPID['U'] = 0.0
    for e in [1.0, 1.0, 1.0, 1.0, 1.0]: # e < EMin, accumulates
        pid_step(pPID, e)
    
    recon_ui = reconstruct_ui(pPID['U'], 1.0, 3.0)
    assert np.isclose(recon_ui, pPID['Ui'])

def test_main(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        meta = {"preset": {"slots": [{"rate": 100}]}}
        meta_file = tmp_path / "f17_synthetic.meta.json"
        with open(meta_file, 'w') as f:
            json.dump(meta, f)
            
        csv_file = tmp_path / "f17_synthetic.slot0.csv"
        # Create a header-only CSV
        with open(csv_file, 'w') as f:
            f.write("t_src_ms,t_host_s,seq,Ctrler.Z_posPID.FB\n")
            
        import sys
        monkeypatch.setattr(sys, 'argv', ['drift_rootcause', '--logs', str(tmp_path)])
        
        main()
        
        captured = capsys.readouterr()
        assert "T1" in captured.out
        assert "T2" in captured.out
        assert "T3" in captured.out
        assert "T4" in captured.out
        assert "T5" in captured.out
        assert "f17_synthetic - skipped: empty" in captured.out

if __name__ == '__main__':
    pytest.main(['-q', '-p', 'no:cacheprovider', __file__])
