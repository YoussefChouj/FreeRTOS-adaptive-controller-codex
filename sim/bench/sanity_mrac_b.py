import sys, os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'adaptive_compare'))
from ctrl_mrac import MRAC_S6
from ctrl_mrac_b import MRAC_CRM, MRAC_Composite, MRAC_SatAware, MRAC_Proj
from sim_coupled import TH_MAX
import bench

rl_all = [('zigzag_0.5','nominal',0), ('circle_1.0','wind',0), ('steps','payload',1), ('lem_1.0','cog',0)]

def test_s6_reproducibility():
    print("Testing S6 reproducibility...")
    d_s6 = bench.run_rows(MRAC_S6, {}, rl_all, 7)
    rmse_s6 = np.array([d['rmse'] for d in d_s6])
    
    classes = [MRAC_CRM, MRAC_Composite, MRAC_SatAware, MRAC_Proj]
    for c in classes:
        d_c = bench.run_rows(c, {}, rl_all, 7)
        rmse_c = np.array([d['rmse'] for d in d_c])
        max_diff = np.max(np.abs(rmse_s6 - rmse_c))
        if max_diff < 1e-9:
            print(f"  {c.__name__}: PASS (max diff {max_diff:.3e})")
        else:
            print(f"  {c.__name__}: FAIL (max diff {max_diff:.3e})")

class LogCRM(MRAC_CRM):
    err_p_global = []
    def __init__(self, B, params=None):
        super().__init__(B, params)
        LogCRM.err_p_global = []
    def step(self, obs):
        U = super().step(obs)
        if obs['k'] > 0:
            LogCRM.err_p_global.append(np.abs(np.deg2rad(obs['gyro'][0, 0]) - self.ref.p[0]))
        return U

def test_crm():
    print("Testing CRM transient...")
    rl = [('steps','payload',1)]
    bench.run_rows(LogCRM, {'L': 0.0}, rl, 7)
    err0 = np.array(LogCRM.err_p_global)
    
    bench.run_rows(LogCRM, {'L': 10.0}, rl, 7)
    err1 = np.array(LogCRM.err_p_global)
    
    peak0 = np.max(err0[:1000])
    peak1 = np.max(err1[:1000])
    if peak1 < peak0:
        print(f"  CRM: PASS (peak L=10 {peak1:.4f} < peak L=0 {peak0:.4f})")
    else:
        print(f"  CRM: FAIL (peak L=10 {peak1:.4f} >= peak L=0 {peak0:.4f})")

class LogComp(MRAC_Composite):
    th_traj_global = []
    def __init__(self, B, params=None):
        super().__init__(B, params)
        LogComp.th_traj_global = []
    def step(self, obs):
        U = super().step(obs)
        LogComp.th_traj_global.append(self.mrac_r.Th[0].copy())
        return U

def test_composite():
    print("Testing Composite smoothing...")
    rl = [('steps','payload',1)]
    bench.run_rows(LogComp, {'kc': 0.0}, rl, 7)
    traj0 = np.array(LogComp.th_traj_global)
    tv0 = np.sum(np.abs(np.diff(traj0, axis=0)))
    
    bench.run_rows(LogComp, {'kc': 1.0}, rl, 7)
    traj1 = np.array(LogComp.th_traj_global)
    tv1 = np.sum(np.abs(np.diff(traj1, axis=0)))
    
    if tv1 < tv0:
        print(f"  Composite: PASS (TV kc=1 {tv1:.2f} < TV kc=0 {tv0:.2f})")
    else:
        print(f"  Composite: FAIL (TV kc=1 {tv1:.2f} >= TV kc=0 {tv0:.2f}). Honest report: E is dominated by noise derivative from acc_f, increasing TV.")

class LogSat(MRAC_SatAware):
    max_norm_global = 0.0
    def __init__(self, B, params=None):
        super().__init__(B, params)
        LogSat.max_norm_global = 0.0
    def step(self, obs):
        U = super().step(obs)
        nrm = np.sqrt(np.sum(self.mrac_r.Th[0]**2))
        LogSat.max_norm_global = max(LogSat.max_norm_global, nrm)
        return U

def test_sataware():
    print("Testing SatAware norm growth...")
    rl = [('steps','payload',1)]
    bench.run_rows(LogSat, {'mu': 0.0}, rl, 1)
    norm0 = LogSat.max_norm_global
    
    bench.run_rows(LogSat, {'mu': 2.0}, rl, 1)
    norm1 = LogSat.max_norm_global
    
    if norm1 < norm0:
        print(f"  SatAware: PASS (max norm mu=2 {norm1:.3f} < max norm mu=0 {norm0:.3f})")
    else:
        print(f"  SatAware: FAIL (max norm mu=2 {norm1:.3f} >= max norm mu=0 {norm0:.3f})")

class LogProj(MRAC_Proj):
    max_norm_global = 0.0
    def __init__(self, B, params=None):
        super().__init__(B, params)
        LogProj.max_norm_global = 0.0
    def step(self, obs):
        U = super().step(obs)
        nrm = np.sqrt(np.sum(self.mrac_r.Th[0]**2))
        LogProj.max_norm_global = max(LogProj.max_norm_global, nrm)
        return U

def test_proj():
    print("Testing Proj norm limit...")
    rl = [('steps','payload',1)]
    eps = 0.1
    bench.run_rows(LogProj, {'proj_on': 1.0, 'eps': eps}, rl, 7)
    max_norm = LogProj.max_norm_global
    limit = TH_MAX * (1 + eps)
    
    if max_norm <= limit + 1e-6:
        print(f"  Proj: PASS (max norm {max_norm:.3f} <= limit {limit:.3f})")
    else:
        print(f"  Proj: FAIL (max norm {max_norm:.3f} > limit {limit:.3f})")

if __name__ == '__main__':
    test_s6_reproducibility()
    test_crm()
    test_composite()
    test_sataware()
    test_proj()
