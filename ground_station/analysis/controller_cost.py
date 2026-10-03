"""Analytic CPU / RAM estimate of adaptive-controller blocks on the STM32F4 rate loop (PROPOSED numbers).

Cost model = the one behind the night report's deployability table (.agent-ops/out/night/REPORT.md:183-184):
add/mul/compare 1 cycle, div/sqrt 14 cycles (Cortex-M4F VDIV/VSQRT), a libm transcendental (expf, logf,
tanhf) 100 cycles, 168 MHz, no load/store or call overhead. With it the sim L1 (8 add, 5 mul, 2 div per axis)
gives the report's 0.49 us for 2 axes. Real cost is higher (memory traffic, calls): measure on target with
mrac_cyc (API/mrac.h:156-166) before trusting any number here.

    python -m ground_station.analysis.controller_cost
"""
from __future__ import annotations

from dataclasses import dataclass

F_CPU_HZ = 168e6
TICK_S = 0.005                  # MRAC_DT, API/mrac.h:113
CYC = dict(add=1, mul=1, div=14, trans=100)


@dataclass(frozen=True)
class Ops:
    add: int = 0
    mul: int = 0
    div: int = 0
    trans: int = 0
    ram_floats: int = 0         # persistent state + tunables per axis

    def __add__(self, o):
        return Ops(self.add + o.add, self.mul + o.mul, self.div + o.div, self.trans + o.trans,
                   self.ram_floats + o.ram_floats)

    def cycles(self):
        return self.add * CYC["add"] + self.mul * CYC["mul"] + self.div * CYC["div"] + self.trans * CYC["trans"]


def law_ops(nf):
    """MRAC_UpdateAxis adaptive law for nf features, worst case (projection band active on every weight).

    Per weight: Phi^2 (1 mul 1 add), grad (1 mul 1 div, mrac.c:470), projection (2 cmp 1 sub 1 div 1 mul,
    mrac.c:215-234), update (6 mul 4 add, mrac.c:492-503), u_ad (2 mul 1 add, mrac.c:530).
    RAM per weight: Phi, Theta, Whatf, gamma, limit, tol, lower (mrac.h:181-194, 239-244).
    """
    return Ops(add=nf * 9, mul=nf * 11, div=nf * 2, ram_floats=nf * 7)


# Fixed part of MRAC_UpdateAxis, type 0: e, e_dot LPF (1 div), tanhf in the regressor and in PBe (1 div),
# freeze/deadzone compares, e-mod, omega_u LPF, clamp (mrac.c:381-547). Scalars: mrac.h:169-259.
AXIS_FIXED = Ops(add=16, mul=10, div=2, trans=2, ram_floats=36)

BLOCKS = {
    # current firmware STRUCT6 (API/mrac_variant.h:10-16)
    "struct6": AXIS_FIXED + law_ops(6),
    # V0 reference model v2: type 2 matrix-P drive (mrac.c:444-455, 2 div) + 2nd-order model + 8-tick delay ring
    "refmodel_v2_delta": Ops(add=8, mul=10, div=2, ram_floats=8 + 4),
    # V1 saturation-aware leakage mu*|dU|*Theta per weight (sim/bench/ctrl_mrac_b.py:109-125)
    "sataware_delta": Ops(add=6 + 2, mul=6 + 2, ram_floats=2),
    # mixer deficit back-projection, once per tick for all axes (TASK/StabilizerTask.c:1391-1406)
    "mixer_deficit_tick": Ops(add=16, mul=4),
    # V2 RBF block of 12 Gaussians on (rate, angle), separable: exp(a)*exp(b) needs 4+3 expf and 12 products
    # (sim_coupled.py:67-84), plus 12 more weights in the law
    "rbf12_delta": Ops(add=14, mul=14 + 12, trans=4 + 3) + law_ops(12),
    # V3 L1 augmentation (sim/bench/c_ref/aug_l1.c via REPORT.md:182-184): 3 state floats + 5 params
    "l1": Ops(add=8, mul=5, div=2, ram_floats=8),
    # 3L layer 1: simulated nominal cascade (2 PIDs, 4-tap delay, lag, 2 integrators; ctrl_mrac3l.py:85-94)
    "mrac3l_layer1_delta": Ops(add=24, mul=16, ram_floats=2 * 6 + 4 + 3),
    # 3L layer 2 or 3: 4-band one-pole bank + energies (ctrl_mrac3l.py:96-105), softmax 4 logf + 4 expf
    # + 4 div (139-146), gate smoothing and Gamma(t)=g@A (157-161)
    "mrac3l_gate_delta": Ops(add=4 + 4 + 8 + 4 + 4 + 24, mul=4 + 4 + 8 + 4 + 4 + 24, div=4 + 1, trans=8,
                             ram_floats=4 + 4 + 4 + 24),
    # sim S6 (REPORT.md:183): 24 add, 25 mul, 2 div, sqrt, tanh -- calibration row only
    "sim_s6_calib": Ops(add=24, mul=25, div=3, trans=1),
}


def us(cycles):
    return cycles / F_CPU_HZ * 1e6


def tick_pct(cycles):
    return 100.0 * cycles / (F_CPU_HZ * TICK_S)


def variant_rows():
    """(variant, axes, cycles per tick, us per tick, % of the 5 ms tick, extra RAM bytes over struct6)."""
    s6 = BLOCKS["struct6"]
    defs = [
        ("struct6 (today, 4 axes)", 4, s6, 0),
        ("V0 refmodel v2 (4 axes)", 4, s6 + BLOCKS["refmodel_v2_delta"], 0),
        ("V1 struct6 + sataware (4 axes)", 4, s6 + BLOCKS["sataware_delta"], BLOCKS["mixer_deficit_tick"].cycles()),
        ("V2 rbf12 roll/pitch + struct6 yaw/z", 2, s6 + BLOCKS["rbf12_delta"], 2 * s6.cycles()),
        ("V3 L1 yaw aug (on top of struct6)", 1, BLOCKS["l1"], 4 * s6.cycles()),
        ("3L layer1 only, roll/pitch", 2, s6 + BLOCKS["mrac3l_layer1_delta"], 2 * s6.cycles()),
        ("3L both gates, roll/pitch", 2, s6 + BLOCKS["mrac3l_layer1_delta"] + BLOCKS["mrac3l_gate_delta"]
         + BLOCKS["mrac3l_gate_delta"] + BLOCKS["mrac3l_layer1_delta"], 2 * s6.cycles()),
    ]
    rows = []
    for name, n_ax, ops, extra_cyc in defs:
        cyc = n_ax * ops.cycles() + extra_cyc
        ram = 4 * (n_ax * ops.ram_floats - (n_ax * s6.ram_floats if ops is not BLOCKS["l1"] else 0))
        rows.append((name, n_ax, cyc, us(cyc), tick_pct(cyc), ram))
    return rows


def main():
    print("| variant | axes | cycles/tick | us/tick | % of 5 ms | extra RAM B |")
    print("|---|---|---|---|---|---|")
    for name, n_ax, cyc, t_us, pct, ram in variant_rows():
        print(f"| {name} | {n_ax} | {cyc} | {t_us:.1f} | {pct:.2f} | {ram:+d} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
