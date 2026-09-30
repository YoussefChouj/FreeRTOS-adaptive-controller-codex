import pytest
from pathlib import Path
import json
from ground_station.analysis.flightlab import compare
from ground_station.analysis.flightlab import pipeline
from ground_station.analysis.flightlab.loaders import LoadError

def test_flatten():
    obj = {
        "a": 1,
        "b": {"c": 2},
        "d": [3, {"e": 4}],
        "f": True,
        "g": None
    }
    flat = compare.flatten(obj)
    assert flat == {
        "a": 1,
        "b.c": 2,
        "d.0": 3,
        "d.1.e": 4,
        "f": True,
        "g": None
    }

def test_compare_resolve_and_logic(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(pipeline, "REPO_ROOT", tmp_path / "repo")
    
    (tmp_path / "repo").mkdir(parents=True)
    
    # Setup metrics files
    dir_a = tmp_path / "reports" / "flight_A"
    dir_a.mkdir(parents=True)
    metrics_a = {
        "flight": {"name": "flight_A", "git": "abcdef1"},
        "num": 0,
        "bool": True,
        "only_a": 1
    }
    (dir_a / "metrics.json").write_text(json.dumps(metrics_a))
    
    dir_b = tmp_path / "reports" / "flight_B"
    dir_b.mkdir(parents=True)
    metrics_b = {
        "flight": {"name": "flight_B", "git": "invalid-hash"},
        "num": 5,
        "bool": False,
        "only_b": 2
    }
    (dir_b / "metrics.json").write_text(json.dumps(metrics_b))
    
    # Missing -> LoadError
    with pytest.raises(LoadError):
        compare.compare("flight_A", "missing_flight")
        
    # Name and dir resolution
    out_path = compare.compare("flight_A", dir_b)
    
    assert Path(out_path).exists()
    md_content = Path(out_path).read_text(encoding="utf-8")
    
    assert "Invalid git hash: invalid-hash" in md_content
    assert "n/a" in md_content # delta % when A == 0
    assert "only_a" in md_content
    assert "only_b" in md_content
    
    # bools not numeric
    assert "bool" in md_content
    assert "Changed Non-Numeric Values" in md_content
    
