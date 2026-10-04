"""Flight telemetry groups: the g_tlm block of API/flight_telemetry.h (WP-37).

The firmware copies the key flight state, once per control tick, into one contiguous struct of float32
(g_tlm, CCM). A subscribe range is one contiguous run of equal-size values, so one range (size 4, count =
float_count) streams a group, and one range streams the whole block; all values of a frame come from the same
tick. Nothing was renamed: the variables the groups copy stay subscribable under their own names.

GROUPS lists the groups in memory order. `symbols()` gives the DWARF paths (`g_tlm.<group>.<field>`, arrays
expanded) that ground_station.livewatch.symbols.SymbolResolver accepts; `offset_floats()` and `float_count`
give the run of a group inside the block. tests/test_telemetry_groups.py keeps this file equal to the header
and to the writer (Tlm_Snapshot in TASK/StabilizerTask.c).
"""
from __future__ import annotations

from dataclasses import dataclass

SYMBOL = "g_tlm"
VALUE_SIZE = 4          # float32
VALUE_FMT = "f"


@dataclass(frozen=True)
class TlmGroup:
    name: str                               # member of FlightTelemetry_t
    ctype: str                              # its struct type
    fields: tuple[tuple[str, int], ...]     # (field, element count), memory order
    doc: str

    @property
    def float_count(self) -> int:
        return sum(n for _, n in self.fields)

    def symbols(self) -> list[str]:
        out: list[str] = []
        for field, n in self.fields:
            base = f"{SYMBOL}.{self.name}.{field}"
            out += [base] if n == 1 else [f"{base}[{i}]" for i in range(n)]
        return out


GROUPS: tuple[TlmGroup, ...] = (
    TlmGroup("att", "TlmAttitude_t",
             (("roll_deg", 1), ("pitch_deg", 1), ("yaw_deg", 1),
              ("rate_x_dps", 1), ("rate_y_dps", 1), ("rate_z_dps", 1)),
             "attitude (deg) and body rates (deg/s, controller frame)"),
    TlmGroup("sp", "TlmSetpoint_t",
             (("roll_deg", 1), ("pitch_deg", 1), ("yaw_deg", 1),
              ("rate_x_dps", 1), ("rate_y_dps", 1), ("rate_z_dps", 1),
              ("x_cm", 1), ("y_cm", 1), ("vx_cms", 1), ("vy_cms", 1), ("z_m", 1), ("vz_mps", 1)),
             "setpoints of every loop (Ctrler.*PID.Des)"),
    TlmGroup("pos", "TlmPosition_t",
             (("x_cm", 1), ("y_cm", 1), ("vx_cms", 1), ("vy_cms", 1), ("z_m", 1), ("vz_mps", 1)),
             "position and velocity feedback (OF world frame, ToF height)"),
    TlmGroup("mot", "TlmMotor_t",
             (("throttle", 1), ("u_roll", 1), ("u_pitch", 1), ("u_yaw", 1),
              ("m1", 1), ("m2", 1), ("m3", 1), ("m4", 1)),
             "controller outputs and motor commands, CCR"),
    TlmGroup("mrac", "TlmMrac_t",
             (("e", 4), ("u_nom", 4), ("u_ad", 4), ("fade", 1)),
             "MRAC per axis (pitch, roll, yaw, z): error, PID output, adaptive correction; simplex fade"),
    TlmGroup("est", "TlmEstimator_t",
             (("kf_x_m", 1), ("kf_y_m", 1), ("kf_vx_mps", 1), ("kf_vy_mps", 1),
              ("kf_bof_x_mps", 1), ("kf_bof_y_mps", 1), ("of_bias_x", 1), ("of_bias_y", 1)),
             "optical-flow KF state and the OF bias"),
    TlmGroup("pwr", "TlmPower_t",
             (("vbat_v", 1), ("thrust_imu_n", 1), ("thrust_be_n", 4), ("mass_hat_kg", 1)),
             "battery voltage and thrust estimates"),
)

TOTAL_FLOATS = sum(g.float_count for g in GROUPS)


def group(name: str) -> TlmGroup:
    for g in GROUPS:
        if g.name == name:
            return g
    raise KeyError(f"no telemetry group {name!r}; have {[g.name for g in GROUPS]}")


def offset_floats(name: str) -> int:
    """Offset of a group inside g_tlm, in floats (add 4x this to the address of g_tlm)."""
    off = 0
    for g in GROUPS:
        if g.name == name:
            return off
        off += g.float_count
    raise KeyError(name)


def all_symbols() -> list[str]:
    return [s for g in GROUPS for s in g.symbols()]
