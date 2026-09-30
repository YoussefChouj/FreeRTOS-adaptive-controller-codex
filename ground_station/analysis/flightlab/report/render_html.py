import html
import base64
from pathlib import Path
from .render_md import build_blocks, _fmt

def _render_blocks_html(blocks, out_dir):
    lines = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            h = b["level"]
            lines.append(f"<h{h}>{html.escape(b['text'])}</h{h}>")
        elif t == "paragraph":
            lines.append(f"<p>{html.escape(b['text'])}</p>")
        elif t == "table":
            if not b["rows"]:
                continue
            lines.append("<table><thead><tr>")
            for h in b["header"]:
                lines.append(f"<th>{html.escape(_fmt(h))}</th>")
            lines.append("</tr></thead><tbody>")
            for r in b["rows"]:
                lines.append("<tr>")
                for cell in r:
                    lines.append(f"<td>{html.escape(_fmt(cell))}</td>")
                lines.append("</tr>")
            lines.append("</tbody></table>")
        elif t == "image":
            # embed as base64
            img_path = out_dir / b['rel_path']
            if img_path.is_file():
                try:
                    with open(img_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    lines.append(f'<img alt="{html.escape(b["alt"])}" src="data:image/png;base64,{b64}">')
                except Exception:
                    lines.append(f'<p>Image missing: {html.escape(b["rel_path"])}</p>')
            else:
                lines.append(f'<p>Image missing: {html.escape(b["rel_path"])}</p>')
        elif t == "details":
            lines.append(f"<details><summary>{html.escape(b['summary'])}</summary>")
            lines.append(_render_blocks_html(b["blocks"], out_dir))
            lines.append("</details>")
        elif t == "bullet_list":
            lines.append("<ul>")
            for item in b["items"]:
                lines.append(f"<li>{html.escape(item)}</li>")
            lines.append("</ul>")
    return "\n".join(lines)

def render(metrics, recs, figs, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    rec_dicts = []
    for r in recs:
        if hasattr(r, "to_dict"):
            rec_dicts.append(r.to_dict())
        else:
            rec_dicts.append(r)
            
    blocks = build_blocks(metrics, rec_dicts, figs, out_dir)
    body = _render_blocks_html(blocks, out_dir)
    
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Flight Report</title>
<style>
body {{ font-family: sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; margin-bottom: 15px; }}
th, td {{ border: 1px solid #ccc; padding: 5px; }}
th {{ background: #eee; }}
img {{ max-width: 100%; height: auto; display: block; margin: 15px 0; }}
details {{ margin-bottom: 10px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    out_path = out_dir / "report.html"
    out_path.write_text(html_content, encoding="utf-8")
    return out_path
