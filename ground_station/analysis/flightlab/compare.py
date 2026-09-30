import json
import re
import os
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
