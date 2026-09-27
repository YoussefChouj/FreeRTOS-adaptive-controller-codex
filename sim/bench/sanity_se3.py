import numpy as np
from ctrl_se3 import SE3ESO
from plant import G, rot

class DebugSE3(SE3ESO):
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.p['wo'] = 0.0
        self.p['kx'] = 0.0
        self.p['kv'] = 0.0

def test_large_angle():
    print("Testing (a) Large angle recovery...")
    ctrl = DebugSE3(1)
    
    rpy = np.array([[60.0, 0.0, 0.0]])
    gyro = np.zeros((1, 3))
    
    o = {
        'k': 0, 'pos': np.zeros((1, 3)), 'vel': np.zeros((1, 3)),
        'rpy': rpy, 'gyro': gyro, 'vbat': np.array([15.4]),
        'ref': {'p': np.zeros((1, 3)), 'v': np.zeros((1, 3)), 'a': np.zeros((1, 3)), 'yaw': np.zeros(1)}
    }
    
    dt = 0.005
    for k in range(1000):
        o['k'] = k
        ret = ctrl.step(o)
        U = ret['U'][0]
        
        accel = np.array([U[0] * 0.14, U[1] * 0.14, U[2] * 0.13]) 
        gyro[0] += accel * dt * 180 / np.pi
        rpy[0] += gyro[0] * dt
        o['rpy'] = rpy
        o['gyro'] = gyro
        
    print(f"Final roll: {rpy[0, 0]:.2f} deg")
    if abs(rpy[0, 0]) < 5.0:
        print("PASS")
    else:
        print("FAIL")

def test_eso():
    print("Testing (b) ESO steady-state error removal...")
    ctrl = SE3ESO(1)
    
    pos = np.zeros((1, 3))
    vel = np.zeros((1, 3))
    d_true = np.array([[0, 0, 5.0]])
    
    o = {
        'k': 0, 'pos': pos, 'vel': vel,
        'rpy': np.zeros((1, 3)), 'gyro': np.zeros((1, 3)), 'vbat': np.array([15.4]),
        'ref': {'p': np.zeros((1, 3)), 'v': np.zeros((1, 3)), 'a': np.zeros((1, 3)), 'yaw': np.zeros(1)}
    }
    
    dt = 0.005
    for k in range(1000):
        o['k'] = k
        o['pos'] = pos
        o['vel'] = vel
        ret = ctrl.step(o)
        
        thr = ret['thr'][0]
        x_act = thr - 2000
        A1, A2 = 0.0007575780669144985, 1.7137109665427505e-06
        T_act = (A1 * x_act + A2 * x_act**2) * 1.0
        actual_u = T_act * 4 / 1.2961
        
        vel += (np.array([0, 0, actual_u]) - np.array([0, 0, 9.81]) + d_true) * dt
        pos += vel * dt
        
    print(f"Final d_hat: {ctrl.d_hat[0]}")
    print(f"Final pos: {pos[0]}")
    if abs(ctrl.d_hat[0, 2] - 5.0) < 0.5 and abs(pos[0, 2]) < 0.1:
        print("PASS")
    else:
        print("FAIL")

if __name__ == '__main__':
    print("Sanity scripts for SE3ESO:")
    test_large_angle()
    test_eso()
