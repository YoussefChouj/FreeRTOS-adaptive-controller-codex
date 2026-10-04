"""Firmware step servers (csrc/sil_server.c): one process per simulated row, stepped in lock-step over binary pipes.

Field order is the enum in sil_server.c (I_* in, O_* out); keep the two lists below in sync with it.
"""
from __future__ import annotations

import struct
import subprocess
from typing import Iterable, Sequence

import numpy as np

from sim.sil import build

IN = ("pit", "rol", "yaw", "gx", "gy", "gz", "x", "y", "z", "vx", "vy", "vz", "tx", "ty", "tz", "set_yaw",
      "ff", "dp", "dr", "dy", "trim_p", "trim_r")
OUT = ("m1", "m2", "m3", "m4", "thr", "unom_p", "unom_r", "unom_y", "unom_z", "corr_p", "corr_r", "corr_y", "corr_z",
       "th_p", "th_r", "th_y", "th_z", "tripped", "inj", "fade", "learn", "pit_des", "rol_des", "gy_des", "gx_des",
       "gz_des", "zp_des", "zr_des", "vid_p", "vid_r", "vid_y", "host_us")
IN_IDX = {k: i for i, k in enumerate(IN)}
OUT_IDX = {k: i for i, k in enumerate(OUT)}
# CtrlerTypeDef member order (Global_file/robot_types.h:45-62), for the 'P' and 'K' ops
MEMBERS = ("pitchPID", "rollPID", "yawPID", "gyroxPID", "gyroyPID", "gyrozPID", "Z_posPID", "Z_ratePID",
           "locxPID", "locyPID", "locxsPID", "locysPID", "stree_yaw_speed", "stree_pitch_speed")
_NOUT_BYTES = 4 * len(OUT)


class Firmware:
    """One sil_server process = one firmware instance (its own Ctrler, mrac_state, ...)."""

    def __init__(self, variant: int = 0):
        self.p = subprocess.Popen([str(build.build(variant))], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  bufsize=0)

    def _call(self, op: bytes, vals: Sequence[float], n_out: int) -> np.ndarray:
        self.p.stdin.write(op + struct.pack(f"<{len(vals)}f", *vals))
        return np.frombuffer(self._read(4 * n_out), "<f4")

    def _read(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.p.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError(f"sil_server exited (code {self.p.poll()})")
            buf += chunk
        return bytes(buf)

    def cmd(self, cmd_id: int, idx: int, val: float) -> bool:
        """Uplink command as the firmware parser applies it (CMD 0x01, 0x0F idx <= 12, 0x1D)."""
        return bool(self._call(b"C", (cmd_id, idx, val), 1)[0])

    def pid(self, member: str, des: float, fb: float) -> np.ndarray:
        """One API/pid.c ComputePID on Ctrler.<member>: (E, SumE, Up, Ui, Ud, U)."""
        return self._call(b"P", (MEMBERS.index(member), des, fb), 6)

    def gains(self, member: str) -> np.ndarray:
        return self._call(b"K", (MEMBERS.index(member),), 3)

    def send_step(self, row: np.ndarray) -> None:
        self.p.stdin.write(b"S" + np.asarray(row, "<f4").tobytes())

    def recv_step(self) -> np.ndarray:
        return np.frombuffer(self._read(_NOUT_BYTES), "<f4")

    def close(self) -> None:
        if self.p.poll() is None:
            try:
                self.p.stdin.write(b"Q")
                self.p.stdin.close()
            except OSError:
                pass
            self.p.wait(timeout=5)


def step_all(fws: Sequence[Firmware], rows: np.ndarray, active: Iterable[int]) -> np.ndarray:
    """One control tick on every active instance: all requests first, then all replies (the processes overlap)."""
    out = np.zeros((len(fws), len(OUT)), np.float32)
    act = list(active)
    for i in act:
        fws[i].send_step(rows[i])
    for i in act:
        out[i] = fws[i].recv_step()
    return out
