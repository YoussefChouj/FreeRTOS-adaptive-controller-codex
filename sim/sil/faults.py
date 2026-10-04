"""Workflow B fault injection on the SIL (WP-42 P2): the real API/wfb_glue.c (wfb_prim + wfb_traj + wfb_safety) in the
loop with the firmware controller and the plant, one fault per run, checking that wfb_safety trips and acts.

Flight: the glue takes off by itself (TAKEOFF prim, the vehicle following the setpoint ideally, as
tests/firmware_host/test_wfb_glue.c does), then the plant starts at the hover point and the closed loop runs:
plant estimates -> wfb_glue_tick -> TWC target = glue setpoint -> controller step -> plant. The GS heartbeat is sent
every HEARTBEAT_MS. A run ends at the first land_req or motor_stop_req: the LANDING phase and DANGEROUS_STOP live in
TASK/StabilizerTask.c and flight_fsm.c, which the SIL does not port, so what follows the request is not simulated.

Faults (all at t_fault after FAULT_AT s of closed-loop hover):
  link_loss   GS heartbeats stop                         -> HEARTBEAT trip, land
  low_v       battery reading steps 16.0 -> 13.5 V        -> LOW_V trip after low_v_hold_s, land
  tilt        motor 1 thrust x0 (plant mloss)             -> TILT trip, motor stop (KILL)
  fence_push  x estimate steps +1.75 m (an OF glitch): outside fence_x by < fence_over_m -> push-back
  fence_over  x estimate steps +2.00 m: outside by more than fence_over_m -> FENCE trip, land in place at once
The glue hovers at the ground-centre origin (wfb_prim), so a fence fault must move the position ESTIMATE, the input
wfb_safety and the controller both read; the vehicle then flies the other way, as after a real estimate glitch.
"crash" is the SIL divergence tilt (true |roll| or |pitch| > 60 deg, plant.crash_check), recorded, not acted on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sim.sil import controllers, fw, scenarios
from sim.sil.engine import TRIM
from sim.sil.plant import DT_C, Plant, plant_to_fw_xy

CMD_PRIM, PRIM_TAKEOFF, PRIM_HEARTBEAT, PRIM_SET_HOVER_Z = 0x1A, 0, 2, 3
PRIM_HOVER = 2                       # WFB_PRIM_HOVER
TRIP = {0: "NONE", 1: "HEARTBEAT", 2: "LOW_V", 3: "AIRBORNE_CAP", 4: "FENCE", 5: "CEILING", 6: "TILT"}
TICK_MS = 5                          # the 200 Hz stabilizer loop (DT_C)
HEARTBEAT_MS = 500                   # the GS heartbeat period (test_wfb_glue.c)
HOVER_Z = scenarios.Z_HOVER          # SET_HOVER_Z before takeoff: the height the SIL scenarios are validated at
FAULT_AT = 3.0                       # s of closed-loop hover before the fault
VBAT_OK, VBAT_LOW = 16.0, 13.5       # V; wfb_safety low_v = 14.0


@dataclass(frozen=True)
class Fault:
    name: str
    doc: str
    est_dx: float = 0.0              # x estimate step at t_fault, m
    vbat: float = VBAT_OK            # battery reading after t_fault, V
    heartbeat_stop: bool = False
    motor_loss: bool = False
    t_max: float = 8.0               # s after t_fault before the run gives up


FAULTS = {f.name: f for f in (
    Fault("link_loss", "GS heartbeats stop", heartbeat_stop=True),
    Fault("low_v", "battery reading 16.0 -> 13.5 V", vbat=VBAT_LOW),
    Fault("tilt", "motor 1 thrust x0", motor_loss=True, t_max=2.0),
    Fault("fence_push", "x estimate +1.75 m (fence 1.6)", est_dx=1.75),
    Fault("fence_over", "x estimate +2.00 m (fence 1.6 + over 0.3)", est_dx=2.00),
)}


@dataclass
class FaultRun:
    fault: Fault
    t: np.ndarray                    # s from the fault (negative before it)
    x_est: np.ndarray                # x the glue and controller read, m
    x_true: np.ndarray               # true x, m
    x_sp: np.ndarray                 # glue setpoint x, m
    tilt: np.ndarray                 # max(|roll|, |pitch|) of the estimate, deg
    trip: np.ndarray                 # g_wfb_status.safety_trip
    push: np.ndarray                 # g_wfb_status.fence_push
    events: dict = field(default_factory=dict)   # first time (s from the fault) of trip/land_req/motor_stop/crash

    @property
    def trip_name(self) -> str:
        return TRIP[int(self.trip[-1])]


def _glue_in(now_ms: int, x: float, y: float, z: float, roll: float, pitch: float, vbat: float,
             airborne: int) -> dict:
    return dict(now_ms=now_ms, x_m=x, y_m=y, z_m=z, roll_deg=roll, pitch_deg=pitch, vbat_v=vbat, yaw_deg=0.0,
                armed=1, motors_idle=1, sbus_live=1, airborne=airborne, rc_override=0)


def _take_off(f: fw.Firmware) -> int:
    """Glue-only takeoff from the origin with an ideal vehicle; returns now_ms on reaching HOVER."""
    now, x, y, z, airborne = 0, 0.0, 0.0, 0.0, 0
    f.glue_cmd(CMD_PRIM, PRIM_HEARTBEAT, 0.0, now)
    f.glue_tick(**_glue_in(now, x, y, z, 0.0, 0.0, VBAT_OK, airborne))          # TAKEOFF needs one snapshot
    if f.glue_cmd(CMD_PRIM, PRIM_SET_HOVER_Z, HOVER_Z, now) != 2 or f.glue_cmd(CMD_PRIM, PRIM_TAKEOFF, 0.0, now) != 2:
        raise RuntimeError("glue refused SET_HOVER_Z or TAKEOFF on the ground")
    for _ in range(4000):
        now += TICK_MS
        if now % HEARTBEAT_MS == 0:
            f.glue_cmd(CMD_PRIM, PRIM_HEARTBEAT, 0.0, now)
        g = f.glue_tick(**_glue_in(now, x, y, z, 0.0, 0.0, VBAT_OK, airborne))
        airborne |= int(g["takeoff_req"])
        if g["setpoint_valid"]:
            x, y, z = g["x_sp_m"], g["y_sp_m"], g["z_sp_m"]
        if g["prim_state"] == PRIM_HOVER:
            return now
    raise RuntimeError("glue never reached HOVER")


def run(fault: Fault, seed: int = 0, ctrl: str = "pid") -> FaultRun:
    spec = controllers.registry()[ctrl]
    q = scenarios.parse("hover").plant_params(seed)
    if fault.motor_loss:
        q.update(mloss_t=FAULT_AT, mloss_idx=0, mloss_eff=0.0)
    f = fw.Firmware(spec.variant)
    try:
        for c in spec.all_cmds():
            f.cmd(*c)
        now = _take_off(f)
        plant = Plant([q], np.array([[0.0, 0.0, HOVER_Z]]))
        row = np.zeros(len(fw.IN), np.float32)
        row[fw.IN_IDX["trim_p"]], row[fw.IN_IDX["trim_r"]] = TRIM
        ix = fw.IN_IDX
        sp = (0.0, 0.0, HOVER_Z)
        n = int((FAULT_AT + fault.t_max) / DT_C)
        rec = {k: np.full(n, np.nan) for k in ("x_est", "x_true", "x_sp", "tilt", "trip", "push")}
        ev: dict[str, float] = {}
        for k in range(n):
            t = k * DT_C
            ts = t - FAULT_AT
            faulted = ts >= 0.0
            now += TICK_MS
            if now % HEARTBEAT_MS == 0 and not (fault.heartbeat_stop and faulted):
                f.glue_cmd(CMD_PRIM, PRIM_HEARTBEAT, 0.0, now)
            o = plant.obs()
            x, y = plant_to_fw_xy(o["pos"][:, 0], o["pos"][:, 1])
            vx, vy = plant_to_fw_xy(o["vel"][:, 0], o["vel"][:, 1])
            xe = float(x[0]) + (fault.est_dx if faulted else 0.0)
            roll, pitch = float(o["rpy"][0, 0]), float(o["rpy"][0, 1])
            g = f.glue_tick(**_glue_in(now, xe, float(y[0]), float(o["pos"][0, 2]), roll, pitch,
                                       fault.vbat if faulted else VBAT_OK, 1))
            if g["setpoint_valid"]:
                sp = (g["x_sp_m"], g["y_sp_m"], g["z_sp_m"])
            row[ix["pit"]], row[ix["rol"]], row[ix["yaw"]] = o["rpy"][0, 1], o["rpy"][0, 0], o["rpy"][0, 2]
            row[ix["gx"]], row[ix["gy"]], row[ix["gz"]] = o["gyro"][0]
            row[ix["x"]], row[ix["y"]], row[ix["z"]] = 100 * xe, 100 * y[0], o["pos"][0, 2]
            row[ix["vx"]], row[ix["vy"]], row[ix["vz"]] = 100 * vx[0], 100 * vy[0], o["vel"][0, 2]
            row[ix["tx"]], row[ix["ty"]], row[ix["tz"]] = 100 * sp[0], 100 * sp[1], sp[2]
            f.send_step(row)
            out = f.recv_step()
            plant.tick(out[None, :4].copy(), t)
            px, _ = plant_to_fw_xy(plant.p_nav[:, 0], plant.p_nav[:, 1])
            rec["x_est"][k], rec["x_true"][k], rec["x_sp"][k] = xe, float(px[0]), sp[0]
            rec["tilt"][k] = max(abs(roll), abs(pitch))
            rec["trip"][k], rec["push"][k] = g["safety_trip"], g["fence_push"]
            if "crash" not in ev and np.abs(plant.e[0, :2]).max() > np.deg2rad(60):
                ev["crash"] = ts
            for name, hit in (("trip", g["safety_trip"] != 0), ("push", g["fence_push"] != 0),
                              ("land_req", g["land_req"] != 0), ("motor_stop", g["motor_stop_req"] != 0)):
                if hit and name not in ev:
                    ev[name] = ts
            if "land_req" in ev or "motor_stop" in ev:
                n = k + 1
                break
    finally:
        f.close()
    t_axis = np.arange(n) * DT_C - FAULT_AT
    return FaultRun(fault, t_axis, *(rec[k][:n] for k in ("x_est", "x_true", "x_sp", "tilt", "trip", "push")), ev)


def summary(r: FaultRun) -> str:
    ev = ", ".join(f"{k} {v:+.3f} s" for k, v in r.events.items()) or "no event"
    return f"{r.fault.name:11s} trip {r.trip_name:9s} {ev}"


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    for flt in FAULTS.values():
        print(summary(run(flt)))
    print(f"{time.perf_counter() - t0:.1f} s")
