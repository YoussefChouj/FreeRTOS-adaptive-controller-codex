import html
from pathlib import Path
from .. import compare

def heading(level, text):
    return {"type": "heading", "level": level, "text": text}

def paragraph(text):
    return {"type": "paragraph", "text": text}

def table(header, rows):
    return {"type": "table", "header": header, "rows": rows}

def image(rel_path, alt):
    return {"type": "image", "rel_path": rel_path, "alt": alt}

def details(summary, blocks):
    return {"type": "details", "summary": summary, "blocks": blocks}

def bullet_list(items):
    return {"type": "bullet_list", "items": items}

def _fmt(val):
    if val is None:
        return "n/a"
    if isinstance(val, float):
        return f"{val:.4g}"
    return str(val)

def _fmt_cell(val):
    return _fmt(val).replace("|", "\\|").replace("\n", "<br>")

def build_blocks(metrics, recs, figs, out_dir):
    blocks = []
    
    # Title
    flight_name = metrics.get("flight", {}).get("name", "Unknown")
    blocks.append(heading(1, f"Flight Report: {flight_name}"))
    
    # Flight header table
    flight_dict = metrics.get("flight", {})
    hdr_keys = list(flight_dict.keys()) + ["mrac_mode", "ctrl_select", "schema_version"]
    hdr_rows = []
    ctrl = metrics.get("controller", {})
    for k in hdr_keys:
        if k in flight_dict:
            hdr_rows.append([k, flight_dict[k]])
        elif k == "mrac_mode":
            hdr_rows.append([k, ctrl.get("mrac_mode")])
        elif k == "ctrl_select":
            hdr_rows.append([k, ctrl.get("ctrl_select")])
        elif k == "schema_version":
            hdr_rows.append([k, metrics.get("schema_version")])
    blocks.append(table(["Field", "Value"], hdr_rows))
    
    # Summary
    blocks.append(heading(2, "Summary"))
    segs = metrics.get("segments", {})
    ab_intervals = segs.get("airborne", [])
    if ab_intervals is None: ab_intervals = []
    ab_total = sum((t1 - t0) for t0, t1 in ab_intervals)
    steady_intervals = segs.get("steady", [])
    if steady_intervals is None: steady_intervals = []
    steady_total = sum((t1 - t0) for t0, t1 in steady_intervals)
    
    sum_items = [
        f"Airborne: {len(ab_intervals)} segments, {ab_total:.4g}s total",
        f"Steady: {len(steady_intervals)} segments, {steady_total:.4g}s total"
    ]
    
    sev_counts = {}
    for r in recs:
        sev_counts[r.get("severity", "unknown")] = sev_counts.get(r.get("severity", "unknown"), 0) + 1
    sum_items.append(f"Recommendations: " + ", ".join(f"{k}: {v}" for k, v in sev_counts.items()))
    
    meta = metrics.get("meta", {})
    run_list = meta.get("plugins_run", [])
    skip_dict = meta.get("plugins_skipped", {})
    fail_list = meta.get("plugins_failed", [])
    
    sum_items.append(f"Plugins run: {', '.join(run_list) if run_list else 'none'}")
    if skip_dict:
        skip_str = ", ".join(f"{k} ({v})" for k, v in skip_dict.items())
        sum_items.append(f"Plugins skipped: {skip_str}")
    if fail_list:
        sum_items.append(f"Plugins failed: {', '.join(fail_list)}")
        
    blocks.append(bullet_list(sum_items))
    
    # Recommendations table
    blocks.append(heading(2, "Recommendations"))
    rec_headers = ["Severity", "ID", "Category", "Target", "Action", "Factor", "Confidence", "Rationale"]
    if not recs:
        blocks.append(paragraph("No recommendations."))
    else:
        rec_rows = []
        for r in recs:
            rec_rows.append([
                r.get("severity"), r.get("id"), r.get("category"), r.get("target"),
                r.get("action"), r.get("factor"), r.get("confidence"), r.get("rationale")
            ])
        blocks.append(table(rec_headers, rec_rows))
        
        # Details blocks for evidence
        for r in recs:
            ev = r.get("evidence", {})
            ev_str = ", ".join(f"{k}: {v}" for k, v in ev.items())
            blocks.append(details(f"Evidence for {r.get('id')}", [paragraph(ev_str)]))
            
    # Data Quality
    blocks.append(heading(2, "Data Quality"))
    dq = metrics.get("data_quality", {})
    slots = dq.get("slots", [])
    if slots:
        slot_keys = []
        for s in slots:
            for k in s.keys():
                if k not in slot_keys and isinstance(s[k], (int, float, str, bool, type(None))):
                    slot_keys.append(k)
        slot_rows = [[s.get(k) for k in slot_keys] for s in slots]
        blocks.append(table(slot_keys, slot_rows))
    
    stuck = dq.get("stuck_vars", [])
    nan_v = dq.get("nan_vars", [])
    dq_items = [
        f"Stuck vars: {', '.join(stuck) if stuck else 'none'}",
        f"NaN vars: {', '.join(nan_v) if nan_v else 'none'}",
        f"Clock drift: {_fmt(dq.get('clock_drift_ppm'))} ppm"
    ]
    blocks.append(bullet_list(dq_items))
    
    # Loops tables
    blocks.append(heading(2, "Loops (Airborne)"))
    loops = metrics.get("loops", {})
    if loops:
        ab_keys = []
        for l_name, l_data in loops.items():
            ab = l_data.get("airborne")
            if ab:
                for k in ab.keys():
                    if k not in ab_keys and isinstance(ab[k], (int, float, str, bool, type(None))):
                        ab_keys.append(k)
        if ab_keys:
            ab_rows = []
            for l_name, l_data in loops.items():
                ab = l_data.get("airborne")
                if ab:
                    ab_rows.append([l_name] + [ab.get(k) for k in ab_keys])
            blocks.append(table(["Loop"] + ab_keys, ab_rows))
            
    blocks.append(heading(2, "Loops (Steady)"))
    if loops:
        st_keys = []
        for l_name, l_data in loops.items():
            st = l_data.get("steady")
            if st:
                for k in st.keys():
                    if k not in st_keys and isinstance(st[k], (int, float, str, bool, type(None))):
                        st_keys.append(k)
        if st_keys:
            st_rows = []
            for l_name, l_data in loops.items():
                st = l_data.get("steady")
                if st:
                    st_rows.append([l_name] + [st.get(k) for k in st_keys])
            blocks.append(table(["Loop"] + st_keys, st_rows))
            
    # Figures
    blocks.append(heading(2, "Figures"))
    if not figs:
        blocks.append(paragraph("No figures."))
    else:
        for f in figs:
            try:
                rel = Path(f).relative_to(out_dir)
            except ValueError:
                rel = Path(f).name
            blocks.append(image(rel.as_posix(), rel.name))
            
    # Warnings
    blocks.append(heading(2, "Warnings"))
    warns = meta.get("warnings", [])
    if not warns:
        blocks.append(paragraph("No warnings."))
    else:
        blocks.append(bullet_list(warns))
        
    # Plugin Details
    blocks.append(heading(2, "Plugin Data"))
    for p in run_list:
        p_data = metrics.get(p, {})
        flat = compare.flatten(p_data)
        if not flat:
            continue
        p_rows = [[k, flat[k]] for k in sorted(flat.keys())]
        blocks.append(details(f"Plugin: {p}", [table(["Path", "Value"], p_rows)]))
        
    return blocks

def _render_blocks_md(blocks):
    lines = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            lines.append("#" * b["level"] + " " + b["text"] + "\n")
        elif t == "paragraph":
            lines.append(b["text"] + "\n")
        elif t == "table":
            if not b["rows"]:
                continue
            hdr = [_fmt_cell(x) for x in b["header"]]
            lines.append("| " + " | ".join(hdr) + " |")
            lines.append("|" + "|".join(["---"] * len(hdr)) + "|")
            for r in b["rows"]:
                lines.append("| " + " | ".join([_fmt_cell(x) for x in r]) + " |")
            lines.append("")
        elif t == "image":
            lines.append(f"![{b['alt']}]({b['rel_path']})\n")
        elif t == "details":
            lines.append(f"<details><summary>{b['summary']}</summary>\n")
            lines.append(_render_blocks_md(b["blocks"]))
            lines.append("</details>\n")
        elif t == "bullet_list":
            for item in b["items"]:
                lines.append(f"- {item}")
            lines.append("")
    return "\n".join(lines)

def render(metrics, recs, figs, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Recs comes in as list[Recommendation] in some places, so we must call .to_dict()
    rec_dicts = []
    for r in recs:
        if hasattr(r, "to_dict"):
            rec_dicts.append(r.to_dict())
        else:
            rec_dicts.append(r)
            
    blocks = build_blocks(metrics, rec_dicts, figs, out_dir)
    md_content = _render_blocks_md(blocks)
    
    out_path = out_dir / "report.md"
    out_path.write_text(md_content, encoding="utf-8")
    return out_path
