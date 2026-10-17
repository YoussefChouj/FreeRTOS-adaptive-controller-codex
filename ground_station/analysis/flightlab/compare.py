import json
import re
import subprocess
from pathlib import Path
from . import pipeline
from .loaders import LoadError

def flatten(obj, prefix=""):
    """
    Flatten a dict/list to scalar leaves using dotted paths.
    """
    res = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_prefix = f"{prefix}.{k}" if prefix else str(k)
            res.update(flatten(v, new_prefix))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            new_prefix = f"{prefix}.{i}" if prefix else str(i)
            res.update(flatten(v, new_prefix))
    elif isinstance(obj, (int, float, str, bool, type(None))):
        res[prefix] = obj
    return res

def _resolve_path(target):
    p = Path(target)
    if p.is_file() and p.name == "metrics.json":
        return p
    if p.is_dir() and (p / "metrics.json").is_file():
        return p / "metrics.json"
    p2 = pipeline.REPORTS_DIR / target / "metrics.json"
    if p2.is_file():
        return p2
    raise LoadError(f"Cannot resolve metrics.json for {target}")

def _fmt_pct(delta, a):
    if a == 0:
        return "n/a"
    return f"{(delta / a * 100):.4g}%"

def _fmt_float(val):
    if isinstance(val, float):
        return f"{val:.4g}"
    return str(val)

def league(dirs, out_dir):
    import json
    import numpy as np
    from pathlib import Path

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        HAS_MPL = True
    except ImportError:
        HAS_MPL = False

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    flights = []

    for d in dirs:
        try:
            p = _resolve_path(d)
            with open(p, "r", encoding="utf-8") as f:
                metrics = json.load(f)
            league_data = metrics.get("league")
            if league_data:
                flights.append({"dir": d, "metrics": metrics, "league": league_data})
        except (OSError, json.JSONDecodeError, KeyError) as e:
            print(f"Skipping {d}: {type(e).__name__}: {e}")

    # Filter by gap fraction
    from ground_station.analysis.flightlab.plugins.league import PROPOSED_GAP_THRES
    valid_flights = []
    excluded_flights = []

    for f in flights:
        gf = f["league"].get("gap_fraction")
        if gf is not None and not np.isnan(gf) and gf > PROPOSED_GAP_THRES:
            excluded_flights.append({"dir": f["dir"], "reason": f"gap fraction {gf:.3f} > {PROPOSED_GAP_THRES}"})
        else:
            valid_flights.append(f)

    # Group by Axis -> Kind -> Flight
    axes = ["roll", "pitch", "yaw", "z", "x", "y"]
    KINDS = {0: "PID", 1: "ADRC", 2: "LQR", 3: "MPC"}

    table_lines = []
    table_lines.append("# League Report")
    table_lines.append("")

    if excluded_flights:
        table_lines.append("## Excluded Flights")
        for ef in excluded_flights:
            table_lines.append(f"- {ef['dir']}: {ef['reason']}")
        table_lines.append("")

    PROPOSED_BOOTSTRAP_RESAMPLES = 1000
    rng = np.random.default_rng(42) # fixed seed

    league_json = {"flights": [f["dir"] for f in flights], "excluded": excluded_flights, "axes": {}}

    for ax in axes:
        ax_data = {}
        # gather e_rms per kind
        for f in valid_flights:
            kinds = f["league"].get("axis_kinds", {})
            kind_int = kinds.get(ax)
            if kind_int == "unknown" or kind_int not in KINDS:
                continue
            kind = KINDS[kind_int]

            mba = f["league"].get("metrics_by_axis", {}).get(ax, {})
            e_rms = mba.get("steady", {}).get("e_rms")
            if e_rms is not None and np.isfinite(e_rms):
                ax_data.setdefault(kind, []).append(e_rms)

        if not ax_data:
            continue

        ranks = []
        for kind, values in ax_data.items():
            if len(values) == 0:
                continue

            v_arr = np.array(values)
            mean_e = np.mean(v_arr)

            # Bootstrap CI
            if len(v_arr) > 1:
                resamples = rng.choice(v_arr, size=(PROPOSED_BOOTSTRAP_RESAMPLES, len(v_arr)), replace=True)
                means = np.mean(resamples, axis=1)
                ci_lo = np.percentile(means, 2.5)
                ci_hi = np.percentile(means, 97.5)
            else:
                ci_lo = mean_e
                ci_hi = mean_e

            ranks.append({"kind": kind, "mean": mean_e, "ci_lo": ci_lo, "ci_hi": ci_hi, "n": len(v_arr)})

        ranks.sort(key=lambda x: x["mean"])

        # Determine ties (overlapping CIs)
        for i in range(len(ranks)):
            ranks[i]["tie"] = False
            for j in range(len(ranks)):
                if i != j:
                    if not (ranks[i]["ci_hi"] < ranks[j]["ci_lo"] or ranks[i]["ci_lo"] > ranks[j]["ci_hi"]):
                        ranks[i]["tie"] = True

        table_lines.append(f"## Axis: {ax}")
        table_lines.append("| Rank | Controller | n | Error RMS | 95% CI | Tie |")
        table_lines.append("|---|---|---|---|---|---|")

        league_json["axes"][ax] = ranks

        for i, r in enumerate(ranks):
            tie_str = "*" if r["tie"] else ""
            table_lines.append(f"| {i+1} | {r['kind']} | {r['n']} | {r['mean']:.4g} | [{r['ci_lo']:.4g}, {r['ci_hi']:.4g}] | {tie_str} |")

        table_lines.append("")

        # Plot
        if HAS_MPL:
            fig, ax_plt = plt.subplots(figsize=(8, 5))
            kinds_labels = [r["kind"] for r in ranks]
            means = [r["mean"] for r in ranks]
            yerr = [[r["mean"] - r["ci_lo"] for r in ranks], [r["ci_hi"] - r["mean"] for r in ranks]]

            ax_plt.bar(kinds_labels, means, yerr=yerr, capsize=5)
            ax_plt.set_ylabel("Error RMS")
            ax_plt.set_title(f"Axis {ax} Comparison")

            plot_path = out_dir / f"league_{ax}.png"
            fig.savefig(plot_path)
            plt.close(fig)

    (out_dir / "league.md").write_text("\n".join(table_lines), encoding="utf-8")
    (out_dir / "league.json").write_text(json.dumps(league_json, indent=2), encoding="utf-8")

    return str(out_dir)


def compare(a, b):
    path_a = _resolve_path(a)
    path_b = _resolve_path(b)
    
    with open(path_a, "r", encoding="utf-8") as f:
        metrics_a = json.load(f)
    with open(path_b, "r", encoding="utf-8") as f:
        metrics_b = json.load(f)
        
    name_a = re.sub(r'[^A-Za-z0-9_.-]', '_', metrics_a.get("flight", {}).get("name", "A"))
    name_b = re.sub(r'[^A-Za-z0-9_.-]', '_', metrics_b.get("flight", {}).get("name", "B"))
    
    out_dir = pipeline.REPORTS_DIR / f"compare_{name_a}_vs_{name_b}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "compare.md"
    
    lines = []
    lines.append(f"# Compare: {name_a} vs {name_b}")
    lines.append(f"- **Preset**: {metrics_a.get('flight', {}).get('preset', 'n/a')} vs {metrics_b.get('flight', {}).get('preset', 'n/a')}")
    lines.append(f"- **Git**: {metrics_a.get('flight', {}).get('git', 'n/a')} vs {metrics_b.get('flight', {}).get('git', 'n/a')}")
    lines.append(f"- **MRAC Mode**: {metrics_a.get('controller', {}).get('mrac_mode', 'n/a')} vs {metrics_b.get('controller', {}).get('mrac_mode', 'n/a')}")
    lines.append("")
    lines.append("metrics.json carries no var list, cannot diff preset vars.")
    lines.append("")
    lines.append("## Git Log")
    
    git_a = metrics_a.get('flight', {}).get('git', '')
    git_b = metrics_b.get('flight', {}).get('git', '')
    if git_a and git_b:
        git_a_clean = git_a.replace("-dirty", "")
        git_b_clean = git_b.replace("-dirty", "")
        if not re.match(r'^[0-9a-f]{7,40}$', git_a_clean):
            lines.append(f"Invalid git hash: {git_a_clean}")
        elif not re.match(r'^[0-9a-f]{7,40}$', git_b_clean):
            lines.append(f"Invalid git hash: {git_b_clean}")
        else:
            try:
                out = subprocess.check_output(
                    ["git", "log", "--oneline", f"{git_a_clean}..{git_b_clean}"],
                    cwd=pipeline.REPO_ROOT,
                    timeout=10,
                    text=True
                )
                if out.strip():
                    lines.append("```text")
                    lines.append(out.strip())
                    lines.append("```")
                else:
                    lines.append("No changes or A is ahead of B.")
            except subprocess.SubprocessError as e:
                lines.append(f"Failed to fetch git log: {e}")
            except OSError as e:
                lines.append(f"Failed to fetch git log (OSError): {e}")
    else:
        lines.append("Missing git hashes.")
        
    lines.append("")
    
    flat_a = flatten(metrics_a)
    flat_b = flatten(metrics_b)
    
    all_keys = set(flat_a.keys()) | set(flat_b.keys())
    
    num_changes = []
    non_num_changes = []
    only_a = []
    only_b = []
    
    for k in sorted(all_keys):
        va = flat_a.get(k)
        vb = flat_b.get(k)
        if va is None and vb is not None and k not in flat_a:
            only_b.append(k)
            continue
        if vb is None and va is not None and k not in flat_b:
            only_a.append(k)
            continue
            
        if va == vb:
            continue
            
        is_num_a = isinstance(va, (int, float)) and not isinstance(va, bool)
        is_num_b = isinstance(vb, (int, float)) and not isinstance(vb, bool)
        
        if is_num_a and is_num_b:
            delta = vb - va
            pct_val = abs(delta / va) if va != 0 else float('inf')
            pct_str = _fmt_pct(delta, va)
            num_changes.append((pct_val, k, va, vb, delta, pct_str))
        else:
            non_num_changes.append((k, va, vb))
            
    num_changes.sort(key=lambda x: x[0], reverse=True)
    
    lines.append("## Top 20 Numeric Changes")
    lines.append("| Path | A | B | Delta | Delta % |")
    lines.append("|---|---|---|---|---|")
    for _, k, va, vb, delta, pct_str in num_changes[:20]:
        lines.append(f"| {k} | {_fmt_float(va)} | {_fmt_float(vb)} | {_fmt_float(delta)} | {pct_str} |")
    lines.append("")
    
    lines.append("## All Numeric Changes")
    lines.append("| Path | A | B | Delta | Delta % |")
    lines.append("|---|---|---|---|---|")
    for _, k, va, vb, delta, pct_str in num_changes:
        lines.append(f"| {k} | {_fmt_float(va)} | {_fmt_float(vb)} | {_fmt_float(delta)} | {pct_str} |")
    lines.append("")
    
    lines.append("## Changed Non-Numeric Values")
    lines.append("| Path | A | B |")
    lines.append("|---|---|---|")
    for k, va, vb in non_num_changes:
        vva = str(va).replace('|', '\\|').replace('\n', '<br>')
        vvb = str(vb).replace('|', '\\|').replace('\n', '<br>')
        lines.append(f"| {k} | {vva} | {vvb} |")
    lines.append("")
    
    lines.append("## Only in A")
    if only_a:
        for k in only_a:
            lines.append(f"- {k}")
    else:
        lines.append("None")
    lines.append("")
    
    lines.append("## Only in B")
    if only_b:
        for k in only_b:
            lines.append(f"- {k}")
    else:
        lines.append("None")
    lines.append("")
    
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)
