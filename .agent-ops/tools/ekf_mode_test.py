"""Grounded EKF bias-mode comparison (disarmed drone, no motor command of any kind).

For each mode in MODES: write g_ekf_gate.bias_mode_req + reinit_req (both applied only on ground by
Ekf9_GateStep), then sample s_ekf / g_ekf_gate / raw OF over the probe for SECS. Per mode it reports
EKF velocity stats and the integrated position creep sum(v*dt) -- the quantity a velocity-hold loop
would turn into drift -- next to the same metrics for the legacy raw OF feedback (ano_of.of2_dx/dy).

    python .agent-ops/tools/ekf_mode_test.py [--secs 120] [--hz 10] [--modes 0,1,2]
"""
import argparse, csv, math, struct, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ground_station.livewatch.probe import ProbeSession
from ground_station.livewatch.symbols import SymbolResolver

VEL_SANE_CMS = 5000.0   # 50 m/s: anything above is a corrupt wireless-SWD read, not a state


def addrs():
    r = SymbolResolver(str(ROOT / "OBJ/JX_FLY.axf"))
    a = lambda n: r.resolve(n)
    return {"x": a("s_ekf.x[0]").address, "P0": a("s_ekf.P[0]").address, "P10": a("s_ekf.P[10]").address,
            "P30": a("s_ekf.P[30]").address, "gate": a("g_ekf_gate").address,
            "req": a("g_ekf_gate.bias_mode_req").address, "reinit": a("g_ekf_gate.reinit_req").address,
            "dx": a("ano_of.of2_dx"), "dy": a("ano_of.of2_dy"), "st": a("s_state").address}


def rd(p, sym):
    raw = bytes(p.read_memory_block8(sym.address, sym.size))
    return struct.unpack("<" + sym.fmt, raw)[0]


def sample(p, A):
    x = struct.unpack("<9f", bytes(p.read_memory_block8(A["x"], 36)))
    g = struct.unpack("<8BIff", bytes(p.read_memory_block8(A["gate"], 20)))
    P = [struct.unpack("<f", bytes(p.read_memory_block8(A[k], 4)))[0] for k in ("P0", "P10", "P30")]
    return x, g, P, rd(p, A["dx"]), rd(p, A["dy"])


def stats(ts, vx, vy):
    n = len(ts)
    mx = sum(vx) / n; my = sum(vy) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in vx) / n); sy = math.sqrt(sum((v - my) ** 2 for v in vy) / n)
    px = py = 0.0
    for i in range(1, n):
        dt = ts[i] - ts[i - 1]
        px += vx[i] * dt; py += vy[i] * dt
    return {"mean_cms": (round(mx, 3), round(my, 3)), "std_cms": (round(sx, 3), round(sy, 3)),
            "max_abs_cms": round(max(max(map(abs, vx)), max(map(abs, vy))), 3),
            "creep_cm": (round(px, 2), round(py, 2)), "creep_norm_cm": round(math.hypot(px, py), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=120.0)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--modes", default="0,1,2")
    a = ap.parse_args()
    modes = [int(m) for m in a.modes.split(",")]
    A = addrs()
    out = ROOT / "logs/flight_tests" / time.strftime("ekf_modes_%Y%m%d_%H%M%S.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    results = {}
    with ProbeSession() as p, open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mode", "t", "vx_cms", "vy_cms", "vz_cms", "bax", "bay", "baz", "bgx", "P00", "P11", "P33",
                    "bias_mode", "bias_frozen", "ctrl_enable", "healthy", "fallback", "gate_vx", "gate_vy",
                    "of2_dx", "of2_dy"])
        for m in modes:
            p.write_memory(A["req"], m, 8)
            p.write_memory(A["reinit"], 1, 8)
            time.sleep(1.0)
            ts, vx, vy, ox, oy, bad = [], [], [], [], [], 0
            t0 = time.time(); last = None
            while time.time() - t0 < a.secs:
                try:
                    x, g, P, dx, dy = sample(p, A)
                except Exception:
                    bad += 1; time.sleep(0.1); continue
                v = (x[0] * 100, x[1] * 100)
                if g[0] != m or not all(abs(q) < VEL_SANE_CMS for q in v) or not all(q == q for q in x):
                    bad += 1; time.sleep(1.0 / a.hz); continue
                t = time.time() - t0
                ts.append(t); vx.append(v[0]); vy.append(v[1]); ox.append(float(dx)); oy.append(float(dy))
                last = (x, g, P)
                w.writerow([m, round(t, 3), v[0], v[1], x[2] * 100, *x[3:7], *P, *g[:3], g[5], g[8], g[9], g[10], dx, dy])
                time.sleep(1.0 / a.hz)
            x, g, P = last
            results[m] = {"n": len(ts), "rejected": bad, "ekf": stats(ts, vx, vy), "legacy_of": stats(ts, ox, oy),
                          "b_a_final": [round(q, 5) for q in x[3:6]], "b_g_final": [round(q, 7) for q in x[6:9]],
                          "P_vv_final": [P[0], P[1]], "P_bax_final": P[2], "bias_frozen": g[6],
                          "healthy": g[5], "fallback_count": g[8]}
            print(f"mode {m}: {results[m]}", flush=True)
        p.write_memory(A["req"], 0, 8)      # leave the default (FIXED) applied
        p.write_memory(A["reinit"], 1, 8)
    print("csv", out)


if __name__ == "__main__":
    main()
