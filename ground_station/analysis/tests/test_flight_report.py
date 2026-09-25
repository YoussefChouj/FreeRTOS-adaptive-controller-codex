"""Tests for ground_station.analysis.flight_report and flight_test_folder."""
from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers — create synthetic session directories
# ---------------------------------------------------------------------------

def _create_synthetic_session(
    base_dir: Path,
    session_label: str = "synthetic-sine-step",
) -> Path:
    """Create a minimal session directory with known signals.

    Generates:
      - telemetry.csv with:
        * roll: sinusoid tracking (setpoint = sin(t))
        * pitch: step response (setpoint jumps from 0 to 10)
        * gyro_x, gyro_y, gyro_z: corresponding rates
        * pid_out_roll/pitch/yaw: PID outputs
        * vbat: slowly decreasing voltage
        * mrac params (roll, pitch, yaw)
        * arm/s_state: disarmed (0) throughout
      - manifest.json

    The session simulates a disarmed bench test (like the dry-bench sample).
    """
    session_dir = base_dir / session_label
    session_dir.mkdir(parents=True, exist_ok=True)

    # Generate telemetry data
    n_points = 1000
    dt_s = 0.02  # 50 Hz
    rows = []
    received_base = 1_000_000_000_000_000_000  # ns

    for i in range(n_points):
        t_ns = received_base + i * int(dt_s * 1e9)
        t_s = i * dt_s

        # --- roll: sinusoid tracking ---
        sp_roll = math.sin(2 * math.pi * 0.5 * t_s)  # 0.5 Hz sine
        me_roll = sp_roll + 0.05 * math.sin(2 * math.pi * 5 * t_s) + 0.02  # small offset error
        # --- pitch: step response (0 -> 10 deg at t=1s) ---
        if t_s < 1.0:
            sp_pitch = 0.0
            me_pitch = 0.0 + 0.1 * math.sin(2 * math.pi * 2 * t_s)
        else:
            sp_pitch = 10.0
            # Exponential settling: 10 * (1 - exp(-5*(t-1)))
            settling = 10.0 * (1 - math.exp(-5.0 * (t_s - 1.0)))
            me_pitch = settling + 0.05 * math.sin(2 * math.pi * 3 * t_s)
        # --- yaw: flat ---
        sp_yaw = 0.0
        me_yaw = 0.0 + 0.01 * math.sin(2 * math.pi * 1 * t_s)
        # --- gyros ---
        gyro_x = me_roll / dt_s if dt_s > 0 else 0
        gyro_y = (me_pitch - (me_pitch - (10.0 if t_s >= 1.0 else 0.0))) / dt_s if dt_s > 0 else 0
        gyro_z = me_yaw / dt_s if dt_s > 0 else 0
        gyro_x_sp = sp_roll / dt_s if dt_s > 0 else 0
        gyro_y_sp = 0.0
        gyro_z_sp = 0.0
        # --- PID outputs ---
        pid_roll = 0.5 * (sp_roll - me_roll)
        pid_pitch = 0.5 * (sp_pitch - me_pitch)
        pid_yaw = 0.3 * (sp_yaw - me_yaw)
        # --- MRAC params ---
        mrac_theta_roll_0 = 1.0 + 0.1 * math.exp(-t_s) * math.sin(t_s)
        mrac_theta_roll_1 = 0.5 + 0.05 * math.exp(-t_s)
        mrac_u_ad_roll = 0.1 * math.sin(2 * math.pi * t_s) * math.exp(-t_s / 2.0)
        mrac_theta_pitch_0 = 2.0 + 0.2 * math.exp(-t_s / 3)
        mrac_theta_pitch_1 = 1.0 + 0.1 * math.exp(-t_s / 3)
        mrac_u_ad_pitch = 0.15 * math.cos(2 * math.pi * t_s) * math.exp(-t_s / 2.0)
        mrac_theta_yaw_0 = 0.8 + 0.05 * math.exp(-t_s / 4)
        mrac_u_ad_yaw = 0.05 * math.sin(2 * math.pi * t_s) * math.exp(-t_s / 3.0)
        # --- vbat ---
        vbat = 12.6 - 0.01 * t_s
        # --- arm ---
        arm = 0.0  # disarmed
        # --- flymode ---
        flymode = 0.0
        # --- motors (simulated throttle proportional to pitch SP) ---
        motor1 = 500 + 10 * (sp_pitch if t_s >= 1.0 else 0) + 5 * math.sin(0.3 * t_s)
        motor2 = 500 + 10 * (sp_pitch if t_s >= 1.0 else 0) + 5 * math.sin(0.3 * t_s + 0.1)
        motor3 = 500 + 10 * (sp_pitch if t_s >= 1.0 else 0) + 5 * math.sin(0.3 * t_s + 0.2)
        motor4 = 505 + 10 * (sp_pitch if t_s >= 1.0 else 0) + 5 * math.sin(0.3 * t_s + 0.3)  # 5 unit offset = asymmetric

        fields = [
            f"{t_ns},0,arm,{arm}",
            f"{t_ns},0,flymode,{flymode}",
            f"{t_ns},0,roll_deg,{me_roll:.6f}",
            f"{t_ns},0,roll_sp,{sp_roll:.6f}",
            f"{t_ns},0,pitch_deg,{me_pitch:.6f}",
            f"{t_ns},0,pitch_sp,{sp_pitch:.6f}",
            f"{t_ns},0,yaw_deg,{me_yaw:.6f}",
            f"{t_ns},0,yaw_sp,{sp_yaw:.6f}",
            f"{t_ns},0,gyro_x,{gyro_x:.6f}",
            f"{t_ns},0,gyro_y,{gyro_y:.6f}",
            f"{t_ns},0,gyro_z,{gyro_z:.6f}",
            f"{t_ns},0,gyro_x_sp,{gyro_x_sp:.6f}",
            f"{t_ns},0,gyro_y_sp,{gyro_y_sp:.6f}",
            f"{t_ns},0,gyro_z_sp,{gyro_z_sp:.6f}",
            f"{t_ns},0,pid.gyrox.U,{pid_roll:.6f}",
            f"{t_ns},0,pid.gyroy.U,{pid_pitch:.6f}",
            f"{t_ns},0,pid.gyroz.U,{pid_yaw:.6f}",
            f"{t_ns},0,mrac.roll.theta_0,{mrac_theta_roll_0:.6f}",
            f"{t_ns},0,mrac.roll.theta_1,{mrac_theta_roll_1:.6f}",
            f"{t_ns},0,mrac.roll.u_ad,{mrac_u_ad_roll:.6f}",
            f"{t_ns},0,mrac.pitch.theta_0,{mrac_theta_pitch_0:.6f}",
            f"{t_ns},0,mrac.pitch.theta_1,{mrac_theta_pitch_1:.6f}",
            f"{t_ns},0,mrac.pitch.u_ad,{mrac_u_ad_pitch:.6f}",
            f"{t_ns},0,mrac.yaw.theta_0,{mrac_theta_yaw_0:.6f}",
            f"{t_ns},0,mrac.yaw.u_ad,{mrac_u_ad_yaw:.6f}",
            f"{t_ns},0,mrac_state,0.0",
            f"{t_ns},0,vbat,{vbat:.4f}",
            f"{t_ns},0,motor1,{motor1:.2f}",
            f"{t_ns},0,motor2,{motor2:.2f}",
            f"{t_ns},0,motor3,{motor3:.2f}",
            f"{t_ns},0,motor4,{motor4:.2f}",
        ]
        rows.extend(fields)

    # Write telemetry.csv
    csv_path = session_dir / "telemetry.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        f.write("received_ns,slot,key,value\n")
        for row in rows:
            f.write(row + "\n")

    # Write manifest.json
    manifest = {
        "label": session_label,
        "reason": "synthetic test fixture",
        "requested_by": "test",
        "started_at": "2026-01-01T00:00:00+00:00",
        "started_at_epoch": 1735689600.0,
        "stopped_at": "2026-01-01T00:20:00+00:00",
        "stopped_at_epoch": 1735690800.0,
        "rows": n_points * 30,
        "schema_version": 1,
    }
    manifest_path = session_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Write events.jsonl
    events_path = session_dir / "events.jsonl"
    with open(events_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "t": 1735689600.0,
            "kind": "recording_start",
            "data": {"reason": "synthetic test fixture"},
        }) + "\n")
        f.write(json.dumps({
            "t": 1735690800.0,
            "kind": "recording_stop",
            "data": {"rows": n_points * 30},
        }) + "\n")

    return session_dir


def _create_minimal_session(base_dir: Path, label: str = "minimal") -> Path:
    """Create a session with only a few keys (tests missing-signal handling)."""
    session_dir = base_dir / label
    session_dir.mkdir(parents=True, exist_ok=True)

    csv_path = session_dir / "telemetry.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        f.write("received_ns,slot,key,value\n")
        f.write("1000000000,0,vbat,12.5\n")
        f.write("1000010000,0,vbat,12.4\n")
        f.write("1000020000,0,vbat,12.3\n")

    manifest = {"label": label, "reason": "minimal test", "rows": 3}
    with open(session_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    return session_dir


# ---------------------------------------------------------------------------
# Tests for flight_report
# ---------------------------------------------------------------------------

class TestFlightReportGenerate:
    """Tests for generate_report()."""

    def test_basic_report_generation(self, tmp_path):
        """A synthetic session produces a report with the expected files."""
        session_dir = _create_synthetic_session(tmp_path)
        out_dir = tmp_path / "report"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(
            str(session_dir),
            out_dir=str(out_dir),
            controller="pid",
            payload="symmetric",
        )

        # Output structure
        assert result["output_dir"] == str(out_dir)
        assert (out_dir / "metadata.json").exists()
        assert (out_dir / "metrics.json").exists()
        assert (out_dir / "metrics.csv").exists()
        assert (out_dir / "summary.md").exists()

        # Metadata contains expected fields
        with open(out_dir / "metadata.json") as f:
            meta = json.load(f)
        assert meta["controller"] == "pid"
        assert meta["payload"] == "symmetric"
        assert "segmentation" in meta

        # Metrics contain attitude data
        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)
        assert "attitude" in metrics
        assert "roll" in metrics["attitude"]
        assert "rmse" in metrics["attitude"]["roll"]
        assert metrics["attitude"]["roll"]["rmse"] > 0

    def test_metrics_have_expected_structure(self, tmp_path):
        """Metrics contain RMSE, MAE, max_abs_error, ITAE, etc."""
        session_dir = _create_synthetic_session(tmp_path)
        out_dir = tmp_path / "report2"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)

        # Attitude metrics
        for axis in ("roll", "pitch", "yaw"):
            assert axis in metrics["attitude"]
            m = metrics["attitude"][axis]
            assert "rmse" in m
            assert "mae" in m
            assert "max_abs_error" in m
            assert "iteae" in m
            assert "steady_state_std" in m
            assert "overshoot_pct" in m

    def test_plot_files_generated(self, tmp_path):
        """Plots directory contains PNG and PDF files."""
        session_dir = _create_synthetic_session(tmp_path)
        out_dir = tmp_path / "report3"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        plots_dir = out_dir / "plots"
        assert plots_dir.exists()
        plot_files = list(plots_dir.glob("*.png"))
        pdf_files = list(plots_dir.glob("*.pdf"))
        assert len(plot_files) > 0, "Expected at least one PNG plot"
        assert len(pdf_files) > 0, "Expected at least one PDF plot"

    def test_missing_signals_graceful(self, tmp_path):
        """A session with only vbat should produce a report without crashing."""
        session_dir = _create_minimal_session(tmp_path, "minimal")
        out_dir = tmp_path / "report_minimal"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        # Should still produce output files
        assert (out_dir / "metrics.json").exists()
        assert (out_dir / "summary.md").exists()

        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)
        # Missing signals should be listed
        assert "missing_signals" in metrics
        # Attitude metrics should be empty
        assert not metrics.get("attitude")

    def test_summary_includes_missing_signals(self, tmp_path):
        """Summary markdown mentions missing signals."""
        session_dir = _create_minimal_session(tmp_path, "minimal2")
        out_dir = tmp_path / "report_min2"

        from ground_station.analysis.flight_report import generate_report
        generate_report(str(session_dir), out_dir=str(out_dir))

        with open(out_dir / "summary.md") as f:
            summary = f.read()
        assert "Missing Signals" in summary

    def test_metrics_csv_format(self, tmp_path):
        """metrics.csv has the expected columns."""
        session_dir = _create_synthetic_session(tmp_path)
        out_dir = tmp_path / "report_csv"

        from ground_station.analysis.flight_report import generate_report
        generate_report(str(session_dir), out_dir=str(out_dir))

        csv_path = out_dir / "metrics.csv"
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["category", "axis", "metric", "value"]
            rows = list(reader)
            assert len(rows) > 0


class TestFlightReportCli:
    """Tests for CLI entry point."""

    def test_cli_help(self, tmp_path):
        """CLI shows usage when called with --help."""
        from ground_station.analysis.flight_report import main
        with pytest.raises(SystemExit):
            main(["--help"])

    def test_cli_no_args(self, tmp_path):
        """CLI exits with error when called without args."""
        from ground_station.analysis.flight_report import main
        with pytest.raises(SystemExit):
            main([])

    def test_cli_on_sample_session(self, tmp_path):
        """Run report on the real bench sample session."""
        # Use the synthetic session
        session_dir = _create_synthetic_session(tmp_path, "cli-test")
        out_dir = tmp_path / "cli-out"

        from ground_station.analysis.flight_report import main
        import io
        from contextlib import redirect_stdout, redirect_stderr

        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                main([str(session_dir), "--out", str(out_dir),
                       "--controller", "pid", "--payload", "symmetric"])
        except SystemExit:
            pass

        # Check output files
        assert (out_dir / "metadata.json").exists()
        assert (out_dir / "metrics.json").exists()
        assert (out_dir / "summary.md").exists()


class TestFlightReportCompare:
    """Tests for --compare mode."""

    def test_compare_reports(self, tmp_path):
        """Compare mode generates comparison plots and summary."""
        sess1 = _create_synthetic_session(tmp_path, "compare-sym")
        sess2 = _create_synthetic_session(tmp_path, "compare-asy")

        out_dir = tmp_path / "compare-out"

        from ground_station.analysis.flight_report import generate_compare_report
        result = generate_compare_report(
            [str(sess1), str(sess2)],
            out_dir=str(out_dir),
            controller_labels=["pid_sym", "pid_asy"],
        )

        assert result["output_dir"] == str(out_dir)
        assert (out_dir / "summary.md").exists()
        assert len(result["sessions"]) == 2


# ---------------------------------------------------------------------------
# Tests for flight_test_folder
# ---------------------------------------------------------------------------

class TestFlightTestFolder:
    """Tests for flight-test folder creation and indexing."""

    def test_create_flight_test_folder(self, tmp_path):
        """Creates the expected folder structure."""
        # Create a fake session directory
        session_dir = tmp_path / "session_001"
        session_dir.mkdir()
        (session_dir / "telemetry.csv").write_text("a,b\n1,2\n")
        (session_dir / "manifest.json").write_text("{}")

        from ground_station.analysis.flight_test_folder import create_flight_test_folder

        # Mock _get_project_root to use tmp_path
        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            ft_dir = create_flight_test_folder(
                session_dir, controller="pid", payload="symmetric"
            )

        # Check structure
        assert ft_dir.exists()
        raw_dir = ft_dir / "raw"
        assert raw_dir.exists()
        assert (raw_dir / "telemetry.csv").exists()

    def test_update_index_csv(self, tmp_path):
        """Appends a row to index.csv."""
        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            from ground_station.analysis.flight_test_folder import update_index_csv
            session_dir = tmp_path / "session_002"
            session_dir.mkdir()
            update_index_csv(
                session_dir, controller="mrac", payload="asymmetric",
                output_path=str(tmp_path / "output")
            )

        # index.csv is under today's date subdirectory
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        index_path = tmp_path / "logs" / "flight_tests" / date_str / "index.csv"
        assert index_path.exists()
        with open(index_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "controller" in header
            row = next(reader)
            assert "mrac" in row

    def test_analysis_status_tracking(self, tmp_path):
        """Status is stored and retrievable."""
        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            from ground_station.analysis.flight_test_folder import (
                load_analysis_status,
                update_analysis_status,
            )

            update_analysis_status("test_session", "pending", "/tmp/out")
            status = load_analysis_status()
            assert "test_session" in status
            assert status["test_session"]["status"] == "pending"

            update_analysis_status("test_session", "done", "/tmp/out/report")
            status = load_analysis_status()
            assert status["test_session"]["status"] == "done"

    def test_run_analysis_tracks_status(self, tmp_path):
        """run_analysis_and_track creates folder and returns a mockable subprocess."""
        session_dir = tmp_path / "session_track"
        session_dir.mkdir()
        (session_dir / "telemetry.csv").write_text("received_ns,slot,key,value\n1000,0,vbat,12.5\n")
        (session_dir / "manifest.json").write_text("{}")

        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            with mock.patch(
                "ground_station.analysis.flight_test_folder._run_analysis_subprocess"
            ) as mock_proc:
                mock_proc.return_value = mock.MagicMock(pid=12345)

                from ground_station.analysis.flight_test_folder import (
                    run_analysis_and_track,
                )
                ft_dir, proc = run_analysis_and_track(
                    session_dir, controller="pid", payload="symmetric"
                )

                assert ft_dir.exists()
                mock_proc.assert_called_once()

    def test_list_flight_tests(self, tmp_path):
        """Returns entries from index.csv files."""
        with mock.patch(
            "ground_station.analysis.flight_test_folder._get_project_root",
            return_value=tmp_path
        ):
            # Create a date directory and index
            date_dir = tmp_path / "logs" / "flight_tests" / "2026-09-25"
            date_dir.mkdir(parents=True)
            index_path = date_dir / "index.csv"
            with open(index_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "controller", "payload", "label",
                                 "session_dir", "output_path", "analysis_status"])
                writer.writerow(["2026-09-25T00:00:00", "pid", "symmetric", "",
                                 str(tmp_path / "s1"), "/tmp/o1", "done"])

            from ground_station.analysis.flight_test_folder import list_flight_tests
            tests = list_flight_tests(date_str="2026-09-25")
            assert len(tests) == 1
            assert tests[0]["controller"] == "pid"


# ---------------------------------------------------------------------------
# Test on real bench sample session
# ---------------------------------------------------------------------------

class TestRealSession:
    """Run the report on the real bench sample session."""

    def test_report_on_bench_sample(self):
        """Generate report on the dry-bench-flight-comprehensive session."""
        bench_session = Path(
            "/mnt/c/Users/Acer/Desktop/UAV_lab/"
            "FreeRTOS-adaptive-controller-codex/"
            "logs/sessions/20260925-121351-dry-bench-flight-comprehensive"
        )
        if not bench_session.exists():
            pytest.skip("Bench sample session not available")

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "bench-report"

            from ground_station.analysis.flight_report import generate_report
            result = generate_report(
                str(bench_session),
                out_dir=str(out_dir),
                controller="pid",
                payload="symmetric",
                notes="dry bench test, disarmed on bench",
            )

            # Check output files exist
            files_produced = [
                out_dir / "metadata.json",
                out_dir / "metrics.json",
                out_dir / "metrics.csv",
                out_dir / "summary.md",
                out_dir / "plots",
            ]
            for fp in files_produced:
                assert fp.exists(), f"Expected {fp} to exist"

            # Check metadata
            with open(out_dir / "metadata.json") as f:
                meta = json.load(f)
            assert meta["controller"] == "pid"
            assert meta["payload"] == "symmetric"

            # Check metrics structure
            with open(out_dir / "metrics.json") as f:
                metrics = json.load(f)
            assert isinstance(metrics, dict)
            assert "missing_signals" in metrics

            # List produced files
            plot_files = list((out_dir / "plots").glob("*")) if (out_dir / "plots").exists() else []
            print(f"\nBench sample report: {len(files_produced)} output dirs/files, "
                  f"{len(plot_files)} plot files")


# ---------------------------------------------------------------------------
# Tests for rpm_signals
# ---------------------------------------------------------------------------

class TestRpmSignals:
    """Tests for ground_station.analysis.rpm_signals module."""

    def test_known_period_gives_expected_rpm(self):
        """A known period_cyc should produce the exact expected RPM value.

        Formula: RPM = 60 * 168_000_000 / period_cyc
        If period_cyc = 10_080_000 => RPM = 1000
        """
        from ground_station.analysis.rpm_signals import compute_rpm

        # period_cyc = 10_080_000 cycles => 1000 RPM
        period = [10_080_000.0]
        edges = [1.0]
        rpms = compute_rpm(period, edges)
        assert len(rpms) == 1
        assert rpms[0] == pytest.approx(1000.0, rel=1e-6)

    def test_period_168e6_gives_60_rpm(self):
        """period_cyc = SystemCoreClock => 60 RPM (1 rev per second)."""
        from ground_station.analysis.rpm_signals import compute_rpm

        period = [168_000_000.0]
        edges = [1.0]
        rpms = compute_rpm(period, edges)
        assert rpms[0] == pytest.approx(60.0, rel=1e-6)

    def test_period_0_gives_nan(self):
        """Zero period must yield NaN, not inf."""
        from ground_station.analysis.rpm_signals import compute_rpm

        period = [0.0, 0, -1.0]
        edges = [1.0, 2.0, 3.0]
        rpms = compute_rpm(period, edges)
        for r in rpms:
            assert math.isnan(r), f"Expected NaN but got {r}"

    def test_frozen_edges_give_nan(self):
        """When edge counter stops increasing, RPM becomes NaN after hold_window."""
        from ground_station.analysis.rpm_signals import compute_rpm

        # period for ~3000 RPM
        period = [3360000.0] * 10
        edges = [100.0] * 10  # frozen at 100

        rpms = compute_rpm(period, edges, hold_window=3)
        # First 2 samples may be valid (holding window = 3)
        # After 3 consecutive no-increase, NaN kicks in
        import math
        nan_count = sum(1 for r in rpms if math.isnan(r))
        assert nan_count >= 7, f"Expected >=7 NaN but got {nan_count} NaN out of {rpms}"

    def test_increasing_edges_not_stale(self):
        """Monotonically increasing edges should never be stale."""
        from ground_station.analysis.rpm_signals import compute_rpm

        period = [3360000.0] * 100
        edges = [float(i) for i in range(1, 101)]  # steadily increasing
        rpms = compute_rpm(period, edges, hold_window=10)
        for r in rpms:
            assert not math.isnan(r), f"Unexpected NaN: {rpms}"

    def test_rpm_metrics(self):
        """rpm_metrics returns expected keys and values."""
        from ground_station.analysis.rpm_signals import rpm_metrics

        valid_rpms = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
        result = rpm_metrics(valid_rpms)
        assert "mean" in result
        assert "std" in result
        assert "max" in result
        assert "stale_fraction" in result
        assert result["mean"] == pytest.approx(3000.0)
        assert result["max"] == pytest.approx(5000.0)
        assert result["stale_fraction"] == 0.0

    def test_rpm_metrics_empty(self):
        """All-NaN input gives zero metrics."""
        from ground_station.analysis.rpm_signals import rpm_metrics

        result = rpm_metrics([float("nan")] * 10)
        assert result["mean"] == 0.0
        assert result["max"] == 0.0
        assert result["stale_fraction"] == 1.0

    def test_asymmetry_index(self):
        """(max-mean - min-mean) / mean(mean) across motors."""
        from ground_station.analysis.rpm_signals import asymmetry_index
        import statistics

        means = {1: 3000.0, 2: 3000.0, 3: 3000.0, 4: 4000.0}
        ai = asymmetry_index(means)
        overall = statistics.mean([3000.0, 3000.0, 3000.0, 4000.0])
        expected = (4000.0 - 3000.0) / overall
        assert ai == pytest.approx(expected, rel=1e-6)

    def test_asymmetry_index_few_motors(self):
        """Fewer than 2 valid means => None."""
        from ground_station.analysis.rpm_signals import asymmetry_index

        means = {1: 3000.0}
        assert asymmetry_index(means) is None


class TestRpmInFlightReport:
    """RPM integration into flight_report (synthetic session)."""

    def _create_rpm_session(self, base_dir: Path, label: str = "rpm-test") -> Path:
        """Create a synthetic session with RPM period/edge keys."""
        session_dir = base_dir / label
        session_dir.mkdir(parents=True, exist_ok=True)

        n_points = 200
        dt_s = 0.02
        rows = []
        received_base = 1_000_000_000_000_000_000

        for i in range(n_points):
            t_ns = received_base + i * int(dt_s * 1e9)
            t_s = i * dt_s

            # RPM ~3000 for motor1 (period = 60*168e6/3000 = 3_360_000)
            rpm1 = 3000.0
            period1 = int(60 * 168_000_000 / rpm1)
            edge1 = float(100 + i)

            # RPM ~3200 for motor2
            rpm2 = 3200.0
            period2 = int(60 * 168_000_000 / rpm2)
            edge2 = float(200 + i)

            # Motor3: edges frozen at sample 150 (stale test)
            period3 = int(60 * 168_000_000 / 3000.0)
            edge3 = float(150) if i >= 150 else float(150 + i)

            # Motor4: period 0 for first 5 samples, then 0 RPM
            if i < 5:
                period4 = 0
            else:
                period4 = int(60 * 168_000_000 / 3100.0)
            edge4 = float(300 + i)

            fields = [
                f"{t_ns},0,arm,0.0",
                f"{t_ns},0,roll_deg,0.0",
                f"{t_ns},0,roll_sp,0.0",
                f"{t_ns},0,vbat,12.6",
                f"{t_ns},0,motor1,{500 + 0.1*i:.2f}",
                f"{t_ns},0,motor2,{500 + 0.1*i:.2f}",
                f"{t_ns},0,motor3,{500 + 0.1*i:.2f}",
                f"{t_ns},0,motor4,{500 + 0.1*i:.2f}",
                f"{t_ns},2,slot2.rpm_dbg_period_cyc[0],{period1:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_edges[0],{edge1:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_period_cyc[1],{period2:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_edges[1],{edge2:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_period_cyc[2],{period3:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_edges[2],{edge3:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_period_cyc[3],{period4:.0f}",
                f"{t_ns},2,slot2.rpm_dbg_edges[3],{edge4:.0f}",
            ]
            rows.extend(fields)

        csv_path = session_dir / "telemetry.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write("received_ns,slot,key,value\n")
            for row in rows:
                f.write(row + "\n")

        manifest = {
            "label": label,
            "reason": "rpm test fixture",
            "rows": len(rows),
        }
        with open(session_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        return session_dir

    def test_rpm_session_produces_metrics(self, tmp_path):
        """A session with RPM data produces rpm metrics in the report."""
        session_dir = self._create_rpm_session(tmp_path)
        out_dir = tmp_path / "rpm-report"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)

        assert "rpm" in metrics
        for motor in ("motor1", "motor2", "motor3", "motor4"):
            assert motor in metrics["rpm"], f"Missing {motor} in rpm metrics"

    def test_rpm_mean_values_are_reasonable(self, tmp_path):
        """RPM means should be close to injected values (~3000, ~3200, etc.)."""
        session_dir = self._create_rpm_session(tmp_path)
        out_dir = tmp_path / "rpm-values"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)

        rpm = metrics["rpm"]
        # Motor1 ~3000 RPM
        assert rpm["motor1"]["mean"] == pytest.approx(3000.0, rel=0.05)
        # Motor2 ~3200 RPM
        assert rpm["motor2"]["mean"] == pytest.approx(3200.0, rel=0.05)

    def test_rpm_stale_fraction_for_frozen_motor(self, tmp_path):
        """Motor3 has frozen edges after sample 150 out of 200 => stale fraction > 0."""
        session_dir = self._create_rpm_session(tmp_path)
        out_dir = tmp_path / "rpm-stale"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)

        stale = metrics["rpm"]["motor3"]["stale_fraction"]
        assert stale > 0.0, f"Expected stale_fraction > 0 but got {stale}"

    def test_rpm_plot_generated(self, tmp_path):
        """RPM plot PNG and PDF are created."""
        session_dir = self._create_rpm_session(tmp_path)
        out_dir = tmp_path / "rpm-plots"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        plots_dir = out_dir / "plots"
        assert plots_dir.exists()
        rpm_png = plots_dir / "motor_rpm.png"
        rpm_pdf = plots_dir / "motor_rpm.pdf"
        assert rpm_png.exists(), "Expected motor_rpm.png"
        assert rpm_pdf.exists(), "Expected motor_rpm.pdf"

    def test_summary_includes_rpm(self, tmp_path):
        """Summary markdown contains Motor RPM section."""
        session_dir = self._create_rpm_session(tmp_path)
        out_dir = tmp_path / "rpm-summary"

        from ground_station.analysis.flight_report import generate_report
        generate_report(str(session_dir), out_dir=str(out_dir))

        with open(out_dir / "summary.md") as f:
            summary = f.read()
        assert "Motor RPM" in summary
        assert "motor1" in summary
        assert "asymmetry_index" in summary

    def test_absent_rpm_keys_degrade_gracefully(self, tmp_path):
        """A session without RPM keys produces a report without crashing."""
        session_dir = _create_synthetic_session(tmp_path, "no-rpm")
        out_dir = tmp_path / "no-rpm-report"

        from ground_station.analysis.flight_report import generate_report
        result = generate_report(str(session_dir), out_dir=str(out_dir))

        assert (out_dir / "metrics.json").exists()
        assert (out_dir / "summary.md").exists()
        # Should not crash; rpm metrics may be absent or empty
        with open(out_dir / "metrics.json") as f:
            metrics = json.load(f)
        # rpm section should exist but be empty
        assert "rpm" in metrics
