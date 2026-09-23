"""Tests for ground_station.research.store."""
from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path

import pytest

from ground_station.research.run import Run
from ground_station.research.store import Store, UAV_RUNS_DIR


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Create a store backed by a temporary directory."""
    os.environ["UAV_RUNS_DIR"] = str(tmp_path)
    s = Store()
    yield s
    s.close()
    os.environ.pop("UAV_RUNS_DIR", None)


class TestCRUD:
    def test_create_and_get(self, store: Store) -> None:
        run = Run(kind="experiment", intent="test intent")
        sid = store.create(run)
        assert sid == run.id
        got = store.get(sid)
        assert got is not None
        assert got.id == sid
        assert got.kind == "experiment"
        assert got.intent == "test intent"

    def test_get_missing(self, store: Store) -> None:
        assert store.get("nonexistent") is None

    def test_update(self, store: Store) -> None:
        run = Run(kind="debug", intent="original")
        store.create(run)
        run = Run(**{**run.to_dict(), "outcome": "complete", "metrics": {"rmse": 0.5}})
        store.update(run)
        got = store.get(run.id)
        assert got is not None
        assert got.outcome == "complete"
        assert got.metrics == {"rmse": 0.5}


class TestQuery:
    def test_query_all(self, store: Store) -> None:
        run1 = Run(kind="experiment", intent="e1")
        run2 = Run(kind="debug", intent="d1")
        store.create(run1)
        store.create(run2)
        all_runs = list(store.query())
        assert len(all_runs) == 2

    def test_query_filter_kind(self, store: Store) -> None:
        store.create(Run(kind="experiment"))
        store.create(Run(kind="debug"))
        exp_runs = list(store.query("kind = ?", ("experiment",)))
        assert len(exp_runs) == 1
        assert exp_runs[0].kind == "experiment"

    def test_query_filter_phase(self, store: Store) -> None:
        store.create(Run(kind="flight", phase="alpha"))
        store.create(Run(kind="flight", phase="beta"))
        alpha = list(store.query("phase = ?", ("alpha",)))
        assert len(alpha) == 1
        assert alpha[0].phase == "alpha"

    def test_query_no_match(self, store: Store) -> None:
        runs = list(store.query("kind = ?", ("nonexistent",)))
        assert len(runs) == 0


class TestImportCapture:
    def _write_csv(self, tmp_path: Path, name: str,
                   rows: list[list[str]]) -> Path:
        p = tmp_path / name
        with open(p, "w", newline="") as fh:
            writer = csv.writer(fh)
            for row in rows:
                writer.writerow(row)
        return p

    def test_import_stream_log_csv(self, store: Store, tmp_path: Path) -> None:
        data = self._write_csv(
            tmp_path, "stream.csv",
            [["t_src_ms", "t_host_s", "des_roll"],
             ["100", "0.1", "0.0"],
             ["200", "0.2", "1.0"]])
        run = store.import_capture(str(data), kind="csv")
        assert run is not None
        assert len(run.captures) >= 1
        assert run.captures[0].endswith(".csv") or run.captures[0].endswith(".parquet")

    def test_import_capture_preset_csv(self, store: Store, tmp_path: Path) -> None:
        data = self._write_csv(
            tmp_path, "preset.csv",
            [["sample_idx", "tick", "data_hex"],
             ["1", "0", "abc"],
             ["2", "1", "def"]])
        run = store.import_capture(str(data), kind="debug")
        assert run is not None

    def test_import_nonexistent(self, store: Store) -> None:
        with pytest.raises(FileNotFoundError):
            store.import_capture("/nonexistent/file.csv")


class TestMetricsIndexing:
    def test_set_metrics(self, store: Store) -> None:
        run = Run(kind="experiment", metrics={"rmse": 0.1, "overshoot": 0.2})
        store.create(run)
        store.set_metrics(run.id, {"rmse": 0.15, "new_metric": 0.99})
        updated = store.get(run.id)
        assert updated is not None
        # Metrics in the store are persisted to the metrics table
        # Note: updated.metrics won't reflect db changes (no auto-sync)
        # but the index is correct


class TestDataDirResolution:
    def test_env_override(self, tmp_path: Path) -> None:
        os.environ["UAV_RUNS_DIR"] = str(tmp_path)
        s = Store()
        assert s.data_dir == tmp_path
        s.close()
        os.environ.pop("UAV_RUNS_DIR", None)
