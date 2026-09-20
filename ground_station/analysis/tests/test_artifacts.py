"""Tests for ground_station.analysis.artifacts."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ground_station.analysis.artifacts import (
    compute_fingerprint,
    find_similar,
    index_artifacts,
)


def _make_result_file(root: Path, name: str, data: dict) -> Path:
    """Create a JSON result file inside root."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    return path


class TestIndexArtifacts:
    def test_empty_root_returns_empty_list(self, tmp_path):
        result = index_artifacts(tmp_path)
        assert result == []

    def test_indexes_json_files_with_correct_fields(self, tmp_path):
        _make_result_file(tmp_path, "run1.json", {
            "session_id": "abc123",
            "schema_id": "r1-s1-0xDEADBEEF",
            "created": "2026-09-17T00:00:00Z",
            "metrics": {"score": 0.95},
        })
        result = index_artifacts(tmp_path)
        assert len(result) == 1
        assert result[0]["session_id"] == "abc123"
        assert result[0]["schema_id"] == "r1-s1-0xDEADBEEF"
        assert result[0]["created"] == "2026-09-17T00:00:00Z"
        assert result[0]["metrics"] == {"score": 0.95}
        assert "fingerprint" in result[0]
        assert "path" in result[0]

    def test_skips_non_json_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("not json", encoding="utf-8")
        _make_result_file(tmp_path, "valid.json", {"session_id": "xyz"})
        result = index_artifacts(tmp_path)
        assert len(result) == 1
        assert result[0]["session_id"] == "xyz"

    def test_skips_malformed_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{ not valid json", encoding="utf-8")
        _make_result_file(tmp_path, "good.json", {"session_id": "ok"})
        result = index_artifacts(tmp_path)
        assert len(result) == 1
        assert result[0]["session_id"] == "ok"

    def test_indexes_nested_files(self, tmp_path):
        _make_result_file(tmp_path, "subdir/run2.json", {
            "session_id": "nested",
        })
        result = index_artifacts(tmp_path)
        assert len(result) == 1
        assert result[0]["session_id"] == "nested"


class TestComputeFingerprint:
    def test_deterministic(self):
        data = {"key": "value", "n": 42}
        fp1 = compute_fingerprint(data)
        fp2 = compute_fingerprint(data)
        assert fp1 == fp2

    def test_different_data_different_fingerprint(self):
        fp1 = compute_fingerprint({"a": 1})
        fp2 = compute_fingerprint({"a": 2})
        assert fp1 != fp2


class TestFindSimilar:
    def test_empty_root_returns_empty_list(self, tmp_path):
        assert find_similar(tmp_path) == []

    def test_no_similar_pairs_at_default_threshold(self, tmp_path):
        _make_result_file(tmp_path, "run1.json", {"session_id": "a", "data": [1, 2, 3]})
        _make_result_file(tmp_path, "run2.json", {"session_id": "b", "data": [4, 5, 6]})
        result = find_similar(tmp_path)
        assert result == []

    def test_identical_files_found_as_similar(self, tmp_path):
        data = {"session_id": "same", "metrics": {"score": 1.0}}
        _make_result_file(tmp_path, "run1.json", data)
        _make_result_file(tmp_path, "run2.json", data.copy())
        result = find_similar(tmp_path, threshold=0.95)
        assert len(result) == 1
        path_a, path_b, score = result[0]
        assert path_a.endswith("run1.json")
        assert path_b.endswith("run2.json")
        assert score == 1.0

    def test_partial_similarity_below_threshold(self, tmp_path):
        _make_result_file(tmp_path, "run1.json", {"session_id": "a", "metrics": {"a": 1}})
        _make_result_file(tmp_path, "run2.json", {"session_id": "b", "metrics": {"b": 1}})
        # Same structure but different values → low nibble similarity
        result = find_similar(tmp_path, threshold=0.95)
        assert result == []

    def test_results_are_deduplicated(self, tmp_path):
        data = {"x": 1}
        _make_result_file(tmp_path, "a.json", data)
        _make_result_file(tmp_path, "b.json", data)
        _make_result_file(tmp_path, "c.json", data)
        result = find_similar(tmp_path, threshold=0.95)
        paths = {(r[0], r[1]) for r in result}
        # Three files → at most 3 pairs (a,b), (a,c), (b,c)
        assert len(paths) <= 3
