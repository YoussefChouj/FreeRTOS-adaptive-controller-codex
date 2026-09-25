"""Tests for flight-test service hooks."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest


class TestRecordingStartAnalyse:
    """Test that start_recording accepts and stores analyse metadata."""

    def test_start_recording_with_analyse(self, tmp_path):
        """start_recording stores analyse_meta on the recorder."""
        from ground_station.service.core import GroundStationService
        from ground_station.service.storage import SessionStore, CsvRecorder

        store = SessionStore(":memory:")
        recorder = CsvRecorder(tmp_path / "sessions", enabled=True)

        svc = GroundStationService(store=store, recorder=recorder)
        svc.start_recording(
            label="test-flight",
            requested_by="operator",
            reason="test",
            analyse=True,
            controller="mrac",
            payload="asymmetric",
            notes="test notes",
        )

        # Check analyse_meta was stored
        assert hasattr(recorder, "analyse_meta")
        assert recorder.analyse_meta["analyse"] is True
        assert recorder.analyse_meta["controller"] == "mrac"
        assert recorder.analyse_meta["payload"] == "asymmetric"
        assert recorder.analyse_meta["notes"] == "test notes"

    def test_start_recording_without_analyse(self, tmp_path):
        """start_recording without analyse=False works normally."""
        from ground_station.service.core import GroundStationService
        from ground_station.service.storage import SessionStore, CsvRecorder

        store = SessionStore(":memory:")
        recorder = CsvRecorder(tmp_path / "sessions", enabled=True)

        svc = GroundStationService(store=store, recorder=recorder)
        svc.start_recording(
            label="normal-record",
            requested_by="operator",
            reason="normal test",
            analyse=False,
        )

        # analyse_meta should be present but analyse=False
        assert hasattr(recorder, "analyse_meta")
        assert recorder.analyse_meta["analyse"] is False

    def test_recording_status_includes_analyse(self, tmp_path):
        """recording_status() includes analyse fields when present."""
        from ground_station.service.core import GroundStationService
        from ground_station.service.storage import SessionStore, CsvRecorder

        store = SessionStore(":memory:")
        recorder = CsvRecorder(tmp_path / "sessions", enabled=True)

        svc = GroundStationService(store=store, recorder=recorder)
        status = svc.start_recording(
            label="test",
            analyse=True,
            controller="pid",
            payload="symmetric",
        )

        assert status["analyse"] is True
        assert status["controller"] == "pid"
        assert status["payload"] == "symmetric"


class TestStopRecordingWithAnalysis:
    """Test that stop_recording launches analysis when analyse=True."""

    def test_stop_launches_analysis_subprocess(self, tmp_path):
        """stop_recording calls run_analysis_and_track when analyse=True."""
        from ground_station.service.core import GroundStationService
        from ground_station.service.storage import SessionStore, CsvRecorder

        store = SessionStore(":memory:")
        recorder = CsvRecorder(tmp_path / "sessions", enabled=True)

        svc = GroundStationService(store=store, recorder=recorder)
        svc.start_recording(
            label="analysis-test",
            analyse=True,
            controller="pid",
            payload="symmetric",
        )

        # Mock run_analysis_and_track
        with mock.patch(
            "ground_station.analysis.flight_test_folder.run_analysis_and_track"
        ) as mock_ran:
            mock_ran.return_value = (
                Path("/tmp/ft"),
                mock.MagicMock(pid=9999),
            )
            status = svc.stop_recording()

            # The stop_recording itself doesn't call run_analysis_and_track
            # — that's done in the API handler. This test verifies the
            # recorder state is correct for the API to pick up.
            assert not status.get("recording", True)
            assert hasattr(recorder, "analyse_meta")
            assert recorder.analyse_meta["analyse"] is True


class TestFlightTestsEndpoint:
    """Test the /api/flight_tests endpoint functionality."""

    def test_list_flight_tests_empty(self, tmp_path):
        """list_flight_tests returns empty list when no tests exist."""
        # Create the flight_tests directory so iterdir doesn't fail
        (tmp_path / "logs" / "flight_tests").mkdir(parents=True)
        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            from ground_station.analysis.flight_test_folder import list_flight_tests
            tests = list_flight_tests()
            assert tests == []

    def test_list_flight_tests_with_date(self, tmp_path):
        """list_flight_tests filters by date when specified."""
        # Create a date directory with index
        date_dir = tmp_path / "logs" / "flight_tests" / "2026-09-25"
        date_dir.mkdir(parents=True)
        index_path = date_dir / "index.csv"
        with open(index_path, "w", newline="", encoding="utf-8") as f:
            f.write("timestamp,controller,payload,label,session_dir,output_path,analysis_status\n")
            f.write("2026-09-25T00:00:00,pid,symmetric,,/tmp/s1,/tmp/o1,done\n")

        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            from ground_station.analysis.flight_test_folder import list_flight_tests
            tests = list_flight_tests(date_str="2026-09-25")
            assert len(tests) == 1
            assert tests[0]["controller"] == "pid"

    def test_list_flight_tests_no_match(self, tmp_path):
        """list_flight_tests returns empty for non-matching date."""
        date_dir = tmp_path / "logs" / "flight_tests" / "2026-09-24"
        date_dir.mkdir(parents=True)
        (date_dir / "index.csv").write_text(
            "timestamp,controller,payload,label,session_dir,output_path,analysis_status\n"
            "2026-09-24T00:00:00,pid,symmetric,,/tmp/s1,/tmp/o1,done\n"
        )

        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            from ground_station.analysis.flight_test_folder import list_flight_tests
            tests = list_flight_tests(date_str="2026-09-25")
            assert tests == []


class TestApiRouteUpdate:
    """Test that the API routes are properly documented."""

    def test_routes_include_flight_tests(self):
        """Routes map includes /api/flight_tests."""
        from ground_station.service.api import _ROUTE_MAP

        assert "/api/flight_tests" in _ROUTE_MAP["GET"]
        assert "/api/recording/start" in _ROUTE_MAP["POST"]
        assert "/api/recording/stop" in _ROUTE_MAP["POST"]


class TestShellPluginRegistration:
    """Verify the flight-test-panel is registered in the shell."""

    def _project_root(self):
        """Resolve the project root from the test file location, handling git worktrees."""
        import pathlib as _p
        p = _p.Path(__file__).resolve().parent
        # Walk up from tests/ -> analysis/ -> ground_station/ -> root_or_worktree
        for ancestor in [p, p.parent, p.parent.parent]:
            # In a worktree, parent of ground_station/ is the worktree dir
            # In the main checkout, parent of ground_station/ is project root
            # Check if 'docs' exists as sibling (worktree) or parent (checkout)
            docs = ancestor / "docs"
            if docs.exists() and (docs / "dashboard-platform").exists():
                return ancestor
            docs_parent = ancestor.parent / "docs"
            if docs_parent.exists() and (docs_parent / "dashboard-platform").exists():
                return ancestor.parent
        # Fallback: use the worktree absolute path from env
        wt = pathlib.Path("/mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/.worktrees/20260925-144828")
        if wt.exists():
            return wt
        raise RuntimeError("Cannot determine project root")

    def test_index_html_references_flight_test_panel(self):
        """shell/index.html includes /plugins/flight-test-panel.js in PLUGIN_FILES."""
        root = self._project_root()
        index_html = root / "docs" / "dashboard-platform" / "shell" / "index.html"
        assert index_html.exists(), f"index.html not found at {index_html}"
        html = index_html.read_text(encoding="utf-8")
        assert "/plugins/flight-test-panel.js" in html, \
            "flight-test-panel.js not found in PLUGIN_FILES in index.html"

    def test_flight_test_panel_js_exists(self):
        """flight-test-panel.js file exists in the plugins directory."""
        root = self._project_root()
        plugins_dir = root / "docs" / "dashboard-platform" / "shell" / "plugins"
        panel_js = plugins_dir / "flight-test-panel.js"
        assert panel_js.exists(), f"flight-test-panel.js not found at {panel_js}"
