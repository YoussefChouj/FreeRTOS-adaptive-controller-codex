import pytest
from pathlib import Path
from ground_station.analysis.flightlab.report import render_md, render_html
from ground_station.analysis.flightlab.registry import Recommendation
import base64

def test_render_md_html_escapes(tmp_path):
    metrics = {
        "flight": {"name": "test_flight"},
        "meta": {
            "plugins_run": ["plugin1"],
            "plugins_skipped": {"plugin2": "No reason|"},
            "warnings": ["Test warning | <script>alert(1)</script>"]
        },
        "segments": {"airborne": [[1, 2]], "steady": None},
        "data_quality": {"clock_drift_ppm": None}
    }
    recs = [
        Recommendation("id1", "critical", "data", "tgt", "investigate", 1.0, {"k": "v|"}, "Rationale | test <script>", "low"),
        {"severity": "warn", "id": "id2", "category": "mrac", "target": "tgt", "action": "decrease", "factor": 0.5, "confidence": "high", "rationale": "r|", "evidence": {}}
    ]
    
    # Create a dummy tiny PNG
    png_data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    fig_path = tmp_path / "test.png"
    fig_path.write_bytes(png_data)
    
    figs = [fig_path]
    
    md_path = render_md.render(metrics, recs, figs, tmp_path)
    html_path = render_html.render(metrics, recs, figs, tmp_path)
    
    assert md_path.exists()
    assert html_path.exists()
    
    md_content = md_path.read_text(encoding="utf-8")
    html_content = html_path.read_text(encoding="utf-8")
    
    # Markdown escapes
    assert "Rationale \\| test <script>" in md_content
    assert "r\\|" in md_content
    assert "n/a" in md_content # For None clock drift
    
    # HTML escapes
    assert "&lt;script&gt;" in html_content
    assert "<script>" not in html_content
    assert "data:image/png;base64," in html_content
    assert "http://" not in html_content
    assert "https://" not in html_content
    assert "n/a" in html_content

def test_skipped_plugin_none_values(tmp_path):
    metrics = {
        "flight": {"name": "test2"},
        "meta": {"plugins_run": [], "plugins_skipped": {"p1": "skip"}},
        "segments": {},
        "data_quality": {},
        "loops": {"loop1": {"steady": {"e_rms": None}}}
    }
    
    md_path = render_md.render(metrics, [], [], tmp_path)
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "p1 (skip)" in md_content
    assert "n/a" in md_content

