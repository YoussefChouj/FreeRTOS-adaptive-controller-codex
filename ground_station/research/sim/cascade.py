import ctypes
import subprocess
import sys
from dataclasses import dataclass

import numpy as np

_libpid = None
_libpid_attempted = False

def load_pid_lib() -> ctypes.CDLL | None:
    global _libpid, _libpid_attempted
    if _libpid_attempted:
        return _libpid
    _libpid_attempted = True
    try:
        from ground_station.research.sim._ccore import build
        lib_path = build.build_pid_lib()
        if lib_path:
            _libpid = ctypes.CDLL(str(lib_path))
    except (OSError, subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"cascade: C PID unavailable ({e}), using PyPid", file=sys.stderr)
        _libpid = None
    except ImportError as e:
        print(f"cascade: C PID unavailable ({e}), using PyPid", file=sys.stderr)
        _libpid = None
    return _libpid

@dataclass
class PidRow:
    Kp: float
    Ki: float
    Kd: float
    UMax: float
    UpMax: float
    UiMax: float
    UdMax: float
    SumEMax: float
    EMin: float

ROWS_3AE4A23 = {
    "pitchPID": PidRow(3.0, 0.02, 8, 200, 200, 10, 10, 120, 3),
    "rollPID": PidRow(3.0, 0.02, 8, 200, 200, 10, 10, 120, 3),
    "gyroxPID": PidRow(5, 0.01, 10, 300, 300, 20, 100, 1000, 2),
    "gyroyPID": PidRow(5, 0.01, 10, 300, 300, 20, 100, 1000, 2),
    "locxPID": PidRow(0.8, 0.01, 4.0, 300, 300, 20, 50, 200, 30),
    "locyPID": PidRow(0.8, 0.01, 4.0, 300, 300, 20, 50, 200, 30),
    "locxsPID": PidRow(3.0, 0, 6.0, 600, 600, 100, 100, 200, 10),
    "locysPID": PidRow(3.0, 0, 6.0, 600, 600, 100, 100, 200, 10)
}

class CPidStruct(ctypes.Structure):
    _fields_ = [
        ("Des", ctypes.c_float),
        ("FB", ctypes.c_float),
        ("Kp", ctypes.c_float),
        ("Ki", ctypes.c_float),
        ("Kd", ctypes.c_float),
        ("Up", ctypes.c_float),
        ("Ui", ctypes.c_float),
        ("Ud", ctypes.c_float),
        ("E", ctypes.c_float),
        ("PreE", ctypes.c_float),
        ("SumE", ctypes.c_float),
        ("U", ctypes.c_float),
        ("UMax", ctypes.c_float),
        ("UpMax", ctypes.c_float),
        ("UiMax", ctypes.c_float),
        ("UdMax", ctypes.c_float),
        ("SumEMax", ctypes.c_float),
        ("EMin", ctypes.c_float),
        ("aw_mode", ctypes.c_int),
        ("Kt", ctypes.c_float)
    ]

class CPid:
    def __init__(self, row: PidRow):
        self._struct = CPidStruct()
        self._struct.Kp = row.Kp
        self._struct.Ki = row.Ki
        self._struct.Kd = row.Kd
        self._struct.UMax = row.UMax
        self._struct.UpMax = row.UpMax
        self._struct.UiMax = row.UiMax
        self._struct.UdMax = row.UdMax
        self._struct.SumEMax = row.SumEMax
        self._struct.EMin = row.EMin
        self._struct.aw_mode = 0
        self._struct.Kt = 0.0
        
    def step(self, des: float, fb: float) -> float:
        self._struct.Des = des
        self._struct.FB = fb
        load_pid_lib().ComputePID(ctypes.byref(self._struct))
        return self._struct.U
        
    @property
    def Des(self) -> float: return self._struct.Des
    @property
    def FB(self) -> float: return self._struct.FB
    @property
    def E(self) -> float: return self._struct.E
    @property
    def SumE(self) -> float: return self._struct.SumE
    @property
    def U(self) -> float: return self._struct.U
    @property
    def Ui(self) -> float: return self._struct.Ui
    @property
    def Up(self) -> float: return self._struct.Up
    @property
    def Ud(self) -> float: return self._struct.Ud
    @Des.setter
    def Des(self, value): self._struct.Des = value
    @FB.setter
    def FB(self, value): self._struct.FB = value
    @SumE.setter
    def SumE(self, value): self._struct.SumE = value

def value_limit(x: float, small: float, big: float) -> float:
    if x < small: return small
    if x > big: return big
    return x

class PyPid:
    def __init__(self, row: PidRow):
        self.Kp = row.Kp
        self.Ki = row.Ki
        self.Kd = row.Kd
        self.UMax = row.UMax
        self.UpMax = row.UpMax
        self.UiMax = row.UiMax
        self.UdMax = row.UdMax
        self.SumEMax = row.SumEMax
        self.EMin = row.EMin
        
        self.Des = 0.0
        self.FB = 0.0
        self.E = 0.0
        self.PreE = 0.0
        self.SumE = 0.0
        self.U = 0.0
        self.Up = 0.0
        self.Ui = 0.0
        self.Ud = 0.0

    def step(self, des: float, fb: float) -> float:
        self.Des = np.float32(des)
        self.FB = np.float32(fb)
        
        if not np.isfinite(self.Des) or not np.isfinite(self.FB) or not np.isfinite(self.SumE) or not np.isfinite(self.PreE):
            self.SumE = np.float32(0.0)
            self.PreE = np.float32(0.0)
            self.E = np.float32(0.0)
            self.Up = np.float32(0.0)
            self.Ui = np.float32(0.0)
            self.Ud = np.float32(0.0)
            self.U = np.float32(0.0)
            return self.U
            
        self.E = np.float32(self.Des - self.FB)
        if not np.isfinite(self.E):
            self.SumE = np.float32(0.0)
            self.PreE = np.float32(0.0)
            self.E = np.float32(0.0)
            self.Up = np.float32(0.0)
            self.Ui = np.float32(0.0)
            self.Ud = np.float32(0.0)
            self.U = np.float32(0.0)
            return self.U

        if ((self.U <= self.UMax and self.E > 0) or (self.U >= -self.UMax and self.E < 0)) and abs(self.E) < self.EMin:
            self.SumE = np.float32(self.SumE + self.E)
            
        self.SumE = np.float32(value_limit(self.SumE, -self.SumEMax, self.SumEMax))
        self.Ui = np.float32(value_limit(np.float32(np.float32(self.Ki) * self.SumE), -self.UiMax, self.UiMax))
        self.Up = np.float32(value_limit(np.float32(np.float32(self.Kp) * self.E), -self.UpMax, self.UpMax))
        self.Ud = np.float32(value_limit(np.float32(np.float32(self.Kd) * np.float32(self.E - self.PreE)), -self.UdMax, self.UdMax))
        
        u_presat = np.float32(np.float32(self.Up + self.Ui) + self.Ud)
        self.U = np.float32(value_limit(u_presat, -self.UMax, self.UMax))
        
        if not np.isfinite(self.U) or not np.isfinite(self.SumE):
            self.SumE = np.float32(0.0)
            self.U = np.float32(0.0)
            
        self.PreE = self.E
        return self.U

def make_pid(row: PidRow, prefer_c: bool = True):
    if prefer_c and load_pid_lib() is not None:
        return CPid(row)
    return PyPid(row)


@dataclass
class AxisPlant:
    gain: float      # rad/s^2 per tick
    tau_m: float     # motor lag
    delay: float     # pure delay in s
    
    # OF model
    of_delay: float  # seconds
    of_noise_std: float
    
    # disturbances
    torque_bias: float = 0.0 # ticks
    lean_offset: float = 0.0 # degrees
    push_accel: float = 0.0  # cm/s^2 (constant or ramp)
    
class CascadeSim:
    def __init__(self, config, dt=0.005, seed=42):
        self.dt = dt
        self.rng = np.random.RandomState(seed)
        self.config = config

def simulate(config, scenario, duration_s, seed=42):
    pass
def gen_square_traj(dt=0.005, speed=0.2):
    # 1 m square, 0.2 m/s. 
    # Side 1: (0,0) -> (1,0) (5s)
    # Side 2: (1,0) -> (1,1) (5s)
    # Side 3: (1,1) -> (0,1) (5s)
    # Side 4: (0,1) -> (0,0) (5s)
    # total 20s. Waypoint every 0.1m doesn't matter for ideal continuous setpoint, 
    # but let's just make it a continuous track.
    T = int(20 / dt)
    traj = []
    for i in range(T):
        t = i * dt
        if t < 5.0:
            x, y = t * speed, 0.0
            vx, vy = speed, 0.0
        elif t < 10.0:
            x, y = 1.0, (t-5) * speed
            vx, vy = 0.0, speed
        elif t < 15.0:
            x, y = 1.0 - (t-10) * speed, 1.0
            vx, vy = -speed, 0.0
        else:
            x, y = 0.0, 1.0 - (t-15) * speed
            vx, vy = 0.0, -speed
        # x, y in meters. The PIDs operate in cm.
        traj.append((x*100, y*100, vx*100, vy*100))
    return traj

def gen_circle_traj(dt=0.005, speed=0.3):
    # 0.5 m radius circle at 0.3 m/s -> omega = speed / radius = 0.3 / 0.5 = 0.6 rad/s
    omega = 0.6
    r = 50.0 # cm
    duration = 2 * np.pi / omega
    T = int(duration / dt)
    traj = []
    for i in range(T):
        t = i * dt
        # start at (r, 0)
        x = r * np.cos(omega * t)
        y = r * np.sin(omega * t)
        vx = -r * omega * np.sin(omega * t)
        vy = r * omega * np.cos(omega * t)
        # shift so it starts at 0,0? No, let it be. Or shift to (0,0) at t=0:
        # x = r * np.cos(omega * t) - r
        traj.append((x - r, y, vx, vy))
    return traj

class OFModel:
    def __init__(self, rng, delay_s, noise_std, dt):
        self.delay_steps = int(delay_s / dt)
        self.noise_std = noise_std
        self.rng = rng
        self.buf_x = []
        self.buf_y = []
    def step(self, true_vx, true_vy):
        noise_x = self.rng.normal(0, self.noise_std)
        noise_y = self.rng.normal(0, self.noise_std)
        self.buf_x.append(true_vx)
        self.buf_y.append(true_vy)
        if len(self.buf_x) > self.delay_steps:
            return self.buf_x.pop(0) + noise_x, self.buf_y.pop(0) + noise_y
        return self.buf_x[0] + noise_x, self.buf_y[0] + noise_y
def simulate(config: dict, scenario: dict, duration_s: float, seed: int = 42) -> dict:
    dt = 0.005 # 5 ms tick
    rng = np.random.RandomState(seed)
    
    # Init PIDs
    rows = config["rows"]
    rollPID = make_pid(rows["rollPID"])
    pitchPID = make_pid(rows["pitchPID"])
    gyroxPID = make_pid(rows["gyroxPID"])
    gyroyPID = make_pid(rows["gyroyPID"])
    locxPID = make_pid(rows["locxPID"])
    locyPID = make_pid(rows["locyPID"])
    locxsPID = make_pid(rows["locxsPID"])
    locysPID = make_pid(rows["locysPID"])
    
    # State
    pos_x, pos_y = 0.0, 0.0
    vel_x, vel_y = 0.0, 0.0
    roll_ang, pitch_ang = 0.0, 0.0
    roll_rate, pitch_rate = 0.0, 0.0
    
    # We will simulate using the K/(s(1+s/p)) plant. 
    # Let's get the plant parameters from calibration or defaults.
    # We will pass these via scenario or config. Let's use config.
    gain_roll = config.get("gain_roll", 165.0 / 1170.0) # approx
    tau_roll = config.get("tau_roll", 1.0 / 19.8)
    delay_roll = config.get("delay_roll", 0.015)
    gain_pitch = config.get("gain_pitch", 185.0 / 1170.0)
    tau_pitch = config.get("tau_pitch", 1.0 / 16.3)
    delay_pitch = config.get("delay_pitch", 0.012)
    
    of_delay = config.get("of_delay", 0.060)
    of_noise = config.get("of_noise", 1.0)
    of = OFModel(rng, of_delay, of_noise, dt)
    
    # Plant buffers for delay
    roll_torque_buf = [0.0] * max(1, int(delay_roll / dt))
    pitch_torque_buf = [0.0] * max(1, int(delay_pitch / dt))
    
    # MRAC
    mrac = config.get("mrac", False)
    tau_mrac = config.get("tau_mrac", 0.1)
    mrac_u_roll = 0.0
    mrac_u_pitch = 0.0
    
    # Disturbances
    lean_offset_roll = scenario.get("lean_offset_roll", 0.0)
    lean_offset_pitch = scenario.get("lean_offset_pitch", 0.0)
    torque_bias_roll = scenario.get("torque_bias_roll", 0.0)
    torque_bias_pitch = scenario.get("torque_bias_pitch", 0.0)
    torque_step_time = scenario.get("torque_step_time", 999999.0)
    push_ramp = scenario.get("push_ramp", (0.0, 0.0))
    traj = scenario.get("trajectory", None)
    
    N = int(duration_s / dt)
    if traj is not None:
        N = min(N, len(traj))
        
    out = {
        "t": np.zeros(N),
        "pos_x": np.zeros(N), "pos_y": np.zeros(N),
        "vel_x": np.zeros(N), "vel_y": np.zeros(N),
        "roll": np.zeros(N), "pitch": np.zeros(N),
        "roll_rate": np.zeros(N), "pitch_rate": np.zeros(N),
        "roll_u": np.zeros(N), "pitch_u": np.zeros(N),
        "gyrox_u": np.zeros(N), "gyroy_u": np.zeros(N),
        "locx_u": np.zeros(N), "locy_u": np.zeros(N),
        "locxs_u": np.zeros(N), "locys_u": np.zeros(N),
        "tar_roll": np.zeros(N), "tar_pitch": np.zeros(N)
    }
    
    for i in range(N):
        t = i * dt
        out["t"][i] = t
        
        # 1. Update setpoints from trajectory
        if traj is not None:
            sp_x, sp_y, sp_vx, sp_vy = traj[i]
        else:
            sp_x, sp_y, sp_vx, sp_vy = 0.0, 0.0, 0.0, 0.0
            
        # 2. Sensor reading
        # of.step returns (vel_x, vel_y) delayed and noisy.
        fb_of_vx, fb_of_vy = of.step(vel_x, vel_y)
        
        # locxPID tracks Right (Y). locyPID tracks -Forward (-X).
        fb_x = pos_y
        fb_y = -pos_x
        fb_vx = fb_of_vy
        fb_vy = -fb_of_vx
        
        # 3. Outer loops (100 Hz = every 2 ticks)
        # We'll just run them at 100 Hz
        if i % 2 == 0:
            if config.get("freeze_outer", False):
                pass
                
            # Setpoints in World frame. traj gives (sp_x, sp_y) which are Forward, Right.
            # Convert to PID frame: sp_x_pid = Right, sp_y_pid = -Forward
            sp_x_pid = sp_y
            sp_y_pid = -sp_x
            sp_vx_pid = sp_vy
            sp_vy_pid = -sp_vx
            
            locx_u = locxPID.step(sp_x_pid, fb_x)
            locy_u = locyPID.step(sp_y_pid, fb_y)
            
            locxs_sp = locx_u
            locys_sp = locy_u
            
            if config.get("vel_ff", False):
                locxs_sp += sp_vx_pid
                locys_sp += sp_vy_pid
                
            locxs_u = locxsPID.step(locxs_sp, fb_vx)
            locys_u = locysPID.step(locys_sp, fb_vy)
            
            if config.get("acc_ff", False) and i > 0 and traj is not None:
                acc_x = (sp_vx - traj[i-1][2]) / (2*dt)
                acc_y = (sp_vy - traj[i-1][3]) / (2*dt)
                # Map to PID frame
                acc_x_pid = acc_y
                acc_y_pid = -acc_x
                locxs_u += acc_x_pid
                locys_u += acc_y_pid
                
            # accel to lean
            des_pitch = -locys_u
            des_roll = -locxs_u
            
            # tar_pitch = atan(des_pitch / 980) in degrees
            tar_pitch = np.degrees(np.arctan(des_pitch / 980.0))
            tar_roll = np.degrees(np.arctan(-des_roll / 980.0))
            
            tar_pitch = np.clip(tar_pitch, -15.0, 15.0)
            tar_roll = np.clip(tar_roll, -15.0, 15.0)
            
            tar_pitch += config.get("trim_ff_pitch", 0.0)
            tar_roll += config.get("trim_ff_roll", 0.0)
            
        # 4. Angle loops (200 Hz)
        # Attitude FB includes lean offset (sensor misalignment)
        fb_pitch = -pitch_ang + lean_offset_pitch
        fb_roll = roll_ang + lean_offset_roll
        
        pitch_u = pitchPID.step(tar_pitch, fb_pitch)
        roll_u = rollPID.step(tar_roll, fb_roll)
        
        # 5. Rate loops (200 Hz)
        fb_pitch_rate = -pitch_rate
        fb_roll_rate = roll_rate
        gyroy_u = gyroyPID.step(pitch_u, fb_pitch_rate)
        gyrox_u = gyroxPID.step(roll_u, fb_roll_rate)
        
        roll_torque = gyrox_u
        pitch_torque = -gyroy_u
        
        if mrac:
            roll_torque += mrac_u_roll
            pitch_torque -= mrac_u_pitch # pitch torque mixer is -u
            
        # 6. Plant
        tb_roll = torque_bias_roll
        tb_pitch = torque_bias_pitch
        if t >= torque_step_time:
            tb_roll *= scenario.get("tb_mult_roll", 1.0)
            tb_pitch *= scenario.get("tb_mult_pitch", 1.0)
            
        roll_torque_buf.append(roll_torque - tb_roll)
        pitch_torque_buf.append(pitch_torque - tb_pitch)
        
        eff_roll_torque = roll_torque_buf.pop(0)
        eff_pitch_torque = pitch_torque_buf.pop(0)
        
        # angular accel = K * torque. lag tau = 1/pole.
        # So d(rate)/dt = (K * torque - rate) / tau
        roll_accel = (gain_roll * eff_roll_torque - roll_rate) / tau_roll
        pitch_accel = (gain_pitch * eff_pitch_torque - pitch_rate) / tau_pitch
        
        roll_rate += roll_accel * dt
        pitch_rate += pitch_accel * dt
        
        roll_ang += roll_rate * dt
        pitch_ang += pitch_rate * dt
        
        # Translational Plant
        # Push ramp
        push = push_ramp[0]
        if push_ramp[1] != push_ramp[0]:
            push += (push_ramp[1] - push_ramp[0]) * min(1.0, t / duration_s)
            
        # acc_x = -g * tan(pitch) + push
        # acc_y = g * tan(roll)
        acc_x = -980.0 * np.tan(np.radians(pitch_ang)) + push
        acc_y = 980.0 * np.tan(np.radians(roll_ang))
        
        vel_x += acc_x * dt
        vel_y += acc_y * dt
        
        pos_x += vel_x * dt
        pos_y += vel_y * dt
        
        # MRAC update
        if mrac:
            # simple torque bias estimator
            # true bias is tb_roll. estimator lags.
            mrac_u_roll += (tb_roll - mrac_u_roll) * (dt / tau_mrac)
            mrac_u_pitch += (tb_pitch - mrac_u_pitch) * (dt / tau_mrac)
            
        # 7. Record
        out["pos_x"][i], out["pos_y"][i] = pos_x, pos_y
        out["vel_x"][i], out["vel_y"][i] = vel_x, vel_y
        out["roll"][i], out["pitch"][i] = roll_ang, pitch_ang
        out["roll_rate"][i], out["pitch_rate"][i] = roll_rate, pitch_rate
        out["roll_u"][i], out["pitch_u"][i] = roll_u, pitch_u
        out["gyrox_u"][i], out["gyroy_u"][i] = gyrox_u, gyroy_u
        out["locx_u"][i], out["locy_u"][i] = locx_u, locy_u
        out["locxs_u"][i], out["locys_u"][i] = locxs_u, locys_u
        out["tar_roll"][i], out["tar_pitch"][i] = tar_roll, tar_pitch
        
    return out
import pathlib
import pandas as pd
import numpy as np

def load_flight(logs_dir, name):
    df_merged = None
    for slot in range(4):
        csv_path = pathlib.Path(logs_dir) / f"{name}.slot{slot}.csv"
        if not csv_path.exists():
            continue
        try:
            df_slot = pd.read_csv(csv_path)
            if len(df_slot) <= 1: continue
            if df_merged is None:
                df_merged = df_slot
            else:
                df_merged = pd.merge(df_merged, df_slot, on='t_src_ms', how='outer', suffixes=('', '_dup'))
                df_merged = df_merged.loc[:, ~df_merged.columns.str.endswith('_dup')]
        except Exception:
            pass
    if df_merged is not None:
        df_merged.sort_values('t_src_ms', inplace=True)
        df_merged.ffill(inplace=True)
        df_merged.bfill(inplace=True)
        df_merged.reset_index(drop=True, inplace=True)
    return df_merged

def hover_mask(df):
    if df is None or len(df) == 0: return slice(0, 0)
    z_col = 'Ctrler.Z_posPID.FB'
    if z_col not in df.columns: return df.index > -1
    p95 = df[z_col].quantile(0.95)
    mask = (df[z_col] > 0.6 * p95).values.copy()
    valid_idx = np.where(mask)[0]
    if len(valid_idx) == 0: return mask
    first, last = valid_idx[0], valid_idx[-1]
    skip = int((last - first) * 0.15)
    mask[:first + skip] = False
    mask[last + 1:] = False
    return mask

def log_targets(df):
    mask = hover_mask(df)
    df_hover = df[mask]
    if len(df_hover) == 0: return {}
    
    roll_err = (df_hover['Ctrler.rollPID.Des'] - df_hover['Ctrler.rollPID.FB']).mean()
    pitch_err = (df_hover['Ctrler.pitchPID.Des'] - df_hover['Ctrler.pitchPID.FB']).mean()
    roll_u = df_hover['Ctrler.rollPID.U'].mean()
    pitch_u = df_hover['Ctrler.pitchPID.U'].mean()
    gyrox_u = df_hover['Ctrler.gyroxPID.U'].mean()
    gyroy_u = df_hover['Ctrler.gyroyPID.U'].mean()
    
    pos_err_x = df_hover['Ctrler.locxPID.Des'] - df_hover['Ctrler.locxPID.FB']
    pos_err_y = df_hover['Ctrler.locyPID.Des'] - df_hover['Ctrler.locyPID.FB']
    pos_err_x_rms = np.sqrt((pos_err_x**2).mean())
    pos_err_y_rms = np.sqrt((pos_err_y**2).mean())
    
    lean_roll = df_hover['Ctrler.rollPID.FB'].mean()
    lean_pitch = df_hover['Ctrler.pitchPID.FB'].mean()
    
    roll_fb = df_hover['Ctrler.rollPID.FB'].values
    sway_freq = np.nan
    if len(roll_fb) > 100:
        dt = (df_hover['t_src_ms'].iloc[-1] - df_hover['t_src_ms'].iloc[0]) / 1000.0 / len(roll_fb)
        if dt > 0:
            fs = 1.0 / dt
            try:
                from scipy import signal
                f, pxx = signal.welch(roll_fb, fs, nperseg=min(len(roll_fb), 4096))
            except ImportError:
                n = min(len(roll_fb), 4096)
                f = np.fft.rfftfreq(n, d=dt)
                pxx = np.abs(np.fft.rfft(roll_fb[:n]))**2
            mask_f = (f >= 0.2) & (f <= 3.0)
            if np.any(mask_f):
                sway_freq = f[mask_f][np.argmax(pxx[mask_f])]
                
    return {
        "roll_err": roll_err, "pitch_err": pitch_err,
        "roll_u": roll_u, "pitch_u": pitch_u,
        "gyrox_u": gyrox_u, "gyroy_u": gyroy_u,
        "pos_err_x_rms": pos_err_x_rms, "pos_err_y_rms": pos_err_y_rms,
        "lean_roll": lean_roll, "lean_pitch": lean_pitch,
        "sway_freq": sway_freq
    }
def calibrate(logs_dir, quick=False):
    import pathlib
    import copy
    
    print("=== Calibration ===")
    
    # 1. Load shadow14
    df14 = load_flight(logs_dir, "f17_hover_shadow14_removed_white_floor_covering_batery_type_2")
    if df14 is None:
        print("missing f17_hover_shadow14_removed_white_floor_covering_batery_type_2")
        return {"rows": ROWS_3AE4A23}
    t14 = log_targets(df14)
    
    df4 = load_flight(logs_dir, "f17_hover_shadow4_removed_white_floor_covering_batery_type_2")
    if df4 is None:
        print("missing f17_hover_shadow4_removed_white_floor_covering_batery_type_2")
        t4 = None
    else:
        t4 = log_targets(df4)
        
    config = {
        "rows": dict(ROWS_3AE4A23),
        "gain_roll": 165.0 / 1170.0,
        "tau_roll": 1.0 / 19.8,
        "delay_roll": 0.015,
        "gain_pitch": 185.0 / 1170.0,
        "tau_pitch": 1.0 / 16.3,
        "delay_pitch": 0.012,
        "of_delay": 0.060,
        "of_noise": 0.2,
        "mrac": False,
        "tau_mrac": 0.5
    }
    
    def sim_metrics(cfg, targets, duration=10.0):
        scene = {
            "lean_offset_roll": targets["lean_roll"],
            "lean_offset_pitch": targets["lean_pitch"],
            "torque_bias_roll": targets["gyrox_u"],
            "torque_bias_pitch": -targets["gyroy_u"],
        }
        res = simulate(cfg, scene, duration)
        idx = int(0.5 * duration / 0.005)
        
        sim_roll_err = np.mean(res["tar_roll"][idx:] - res["roll"][idx:])
        sim_roll_u = np.mean(res["roll_u"][idx:])
        sim_gyrox_u = np.mean(res["gyrox_u"][idx:])
        sim_pitch_err = np.mean(res["tar_pitch"][idx:] - res["pitch"][idx:])
        sim_pos_x_rms = np.sqrt(np.mean(res["pos_x"][idx:]**2))
        sim_pos_y_rms = np.sqrt(np.mean(res["pos_y"][idx:]**2))
        
        roll_fb = res["roll"][idx:] + scene["lean_offset_roll"]
        sway_freq = np.nan
        dt = 0.005
        fs = 1.0 / dt
        try:
            from scipy import signal
            f, pxx = signal.welch(roll_fb, fs, nperseg=min(len(roll_fb), 1024))
            mask_f = (f >= 0.2) & (f <= 3.0)
            if np.any(mask_f):
                sway_freq = f[mask_f][np.argmax(pxx[mask_f])]
        except ImportError:
            n = min(len(roll_fb), 1024)
            f = np.fft.rfftfreq(n, d=dt)
            pxx = np.abs(np.fft.rfft(roll_fb[:n]))**2
            mask_f = (f >= 0.2) & (f <= 3.0)
            if np.any(mask_f):
                sway_freq = f[mask_f][np.argmax(pxx[mask_f])]
                
        return {
            "roll_err": sim_roll_err, "pitch_err": sim_pitch_err,
            "roll_u": sim_roll_u, "gyrox_u": sim_gyrox_u,
            "pos_err_x_rms": sim_pos_x_rms, "pos_err_y_rms": sim_pos_y_rms,
            "sway_freq": sway_freq
        }

    def loss(x):
        cfg = copy.deepcopy(config)
        cfg["gain_roll"] = x[0]
        cfg["tau_roll"] = x[1]
        cfg["delay_roll"] = x[2]
        cfg["gain_pitch"] = x[3]
        cfg["tau_pitch"] = x[4]
        cfg["delay_pitch"] = x[5]
        cfg["of_delay"] = x[6]
        cfg["of_noise"] = x[7]
        
        sim14 = sim_metrics(cfg, t14, duration=5.0 if quick else 10.0)
        e = 0.0
        e += (sim14["roll_err"] - t14["roll_err"])**2
        e += (sim14["pos_err_y_rms"] - t14["pos_err_y_rms"])**2 * 0.1
        if not np.isnan(sim14["sway_freq"]) and not np.isnan(t14["sway_freq"]):
            e += (sim14["sway_freq"] - t14["sway_freq"])**2 * 10.0
            
        if t4 is not None:
            sim4 = sim_metrics(cfg, t4, duration=5.0 if quick else 10.0)
            e += (sim4["roll_err"] - t4["roll_err"])**2
            e += (sim4["pos_err_y_rms"] - t4["pos_err_y_rms"])**2 * 0.1
        return e

    x0 = [config["gain_roll"], config["tau_roll"], config["delay_roll"],
          config["gain_pitch"], config["tau_pitch"], config["delay_pitch"],
          config["of_delay"], config["of_noise"]]
    
    try:
        from scipy import optimize
        res = optimize.minimize(loss, x0, method="Nelder-Mead", options={"maxiter": 10 if quick else 50})
        xopt = res.x
    except ImportError:
        xopt = x0
        
    config["gain_roll"] = xopt[0]
    config["tau_roll"] = max(0.001, xopt[1])
    config["delay_roll"] = max(0.0, xopt[2])
    config["gain_pitch"] = xopt[3]
    config["tau_pitch"] = max(0.001, xopt[4])
    config["delay_pitch"] = max(0.0, xopt[5])
    config["of_delay"] = max(0.0, xopt[6])
    config["of_noise"] = max(0.0, xopt[7])
    
    print(f"Target: shadow14")
    sim14 = sim_metrics(config, t14, duration=10.0)
    print("metric | sim | log | rel err")
    print(f"roll_err | {sim14['roll_err']:.2f} | {t14['roll_err']:.2f} | {abs(sim14['roll_err'] - t14['roll_err'])/(abs(t14['roll_err'])+1e-6):.2f}")
    print(f"pos_err_y_rms | {sim14['pos_err_y_rms']:.2f} | {t14['pos_err_y_rms']:.2f} | {abs(sim14['pos_err_y_rms'] - t14['pos_err_y_rms'])/(t14['pos_err_y_rms']+1e-6):.2f}")
    print(f"sway_freq | {sim14['sway_freq']:.2f} | {t14['sway_freq']:.2f} | {abs(sim14['sway_freq'] - t14['sway_freq'])/(t14['sway_freq']+1e-6):.2f}")
    
    if t4 is not None:
        print(f"Target: shadow4")
        sim4 = sim_metrics(config, t4, duration=10.0)
        print("metric | sim | log | rel err")
        print(f"roll_err | {sim4['roll_err']:.2f} | {t4['roll_err']:.2f} | {abs(sim4['roll_err'] - t4['roll_err'])/(abs(t4['roll_err'])+1e-6):.2f}")

    # mrac
    df15 = load_flight(logs_dir, "f17_hover_active15_removed_white_floor_covering_batery_type_2")
    if df15 is None:
        print("missing f17_hover_active15_removed_white_floor_covering_batery_type_2")
    else:
        t15 = log_targets(df15)
        # fit tau_mrac
        config["mrac"] = True
        config["tau_mrac"] = 2.0
        # ideally we could fit it, but for now just use a constant or minimal search
        sim15 = sim_metrics(config, t15, duration=10.0)
        print(f"Target: active15 (MRAC)")
        print("metric | sim | log | rel err")
        print(f"gyrox_u | {sim15['gyrox_u']:.2f} | {t15['gyrox_u']:.2f} | {abs(sim15['gyrox_u'] - t15['gyrox_u'])/(abs(t15['gyrox_u'])+1e-6):.2f}")
        
    return config
