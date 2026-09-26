"""E2E validation test: spin up an in-process service+simulator, run the validator.

Starts a ``GroundStationService`` on an ephemeral port, feeds simulated
telemetry via ``service.ingest_decoded()``, and runs the e2e validator
against it.  Marked ``slow`` because the full check set takes 20-30 s.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from ground_station.comm.frame_simulator import build_frame_a, build_frame_b
from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.storage import CsvRecorder, SessionStore


# ── helpers ────────────────────────────────────────────────────────────────

def _find_free_port() -> int:
    """Return a free TCP port, then close the socket."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def _make_service(tmp_path: Path) -> GroundStationService:
    """Create a service with a real recorder (so recording actually writes)."""
    schema = StreamSchema(
        1, 1, 4,
        (
            StreamRange(0x20000000, 4, 1, "altitude", "f"),
            StreamRange(0x20000004, 4, 1, "vx", "f"),
            StreamRange(0x20000008, 4, 1, "vy", "f"),
            StreamRange(0x2000000C, 4, 1, "vz", "f"),
        ),
        0,
    )
    rec = CsvRecorder(tmp_path / "sessions", enabled=True, flush_interval_s=0.1)
    store = SessionStore()
    return GroundStationService(
        store=store, schemas=[schema], source="sim", recorder=rec,
    )


def _feed_simulated_data(service: GroundStationService, count: int = 50) -> None:
    """Feed simulated Frame A + Frame B data into the service."""
    for i in range(count):
        t_s = i * 0.01  # 100 Hz
        # Frame A (sidebar tag "a" -> slot 0)
        # Use monotonically changing values so we can detect change
        service.ingest_decoded("a", {
            "c.altitude":   100.0 + i * 0.1,
            "c.gyro_x":     0.1 * i,
            "c.gyro_y":     0.2 * i,
            "c.gyro_z":     0.3 * i,
            "status.arm":     0.0,
            "status.flymode": 2,
            "status.sbus_lost": 0,
            "status.twc_execute": 0,
            "status.twc_arrived": 1 if i % 3 == 0 else 0,
            "status.rc_authority": 1,
            "status.of_hold": 0,
            "status.estimator_ready": 1,
            "status.motor_idle": i % 2,  # toggle between 0 and 1
            "status.vbat":    16.0 - 0.5 * (i % 5) / 5.0,
            "status.roll_deg":  -0.5 + 0.1 * (i % 10) / 10.0,
            "status.pitch_deg": -1.0 + 0.2 * (i % 8) / 8.0,
            "status.yaw_deg":   16.0 + 0.3 * (i % 6) / 6.0,
        }, time_ns=time.time_ns())

        # Frame B (tag "b" -> slot 1)
        service.ingest_decoded("b", {
            "pid.pitch.FB":  0.1 * i,
            "pid.pitch.Des": 0.2 * i,
            "pid.pitch.U":   0.3 * i,
            "mrac.roll.u_ad": 0.05 * (i % 20),
            "mrac.pitch.u_ad": 0.06 * (i % 20),
        }, time_ns=time.time_ns())


def _continuously_feed(service: GroundStationService, stop_event, start_offset: int = 100) -> None:
    """Continuously feed simulated data in a background thread.
    
    Called after the initial burst to keep values changing during test checks.
    """
    i = start_offset
    while not stop_event.is_set():
        service.ingest_decoded("a", {
            "c.altitude":   100.0 + i * 0.1,
            "status.arm": 0.0,
            "status.motor_idle": i % 2,
            "status.estimator_ready": 1,
        }, time_ns=time.time_ns())
        service.ingest_decoded("b", {
            "pid.pitch.FB":  0.1 * i,
            "mrac.roll.u_ad": 0.05 * (i % 20),
        }, time_ns=time.time_ns())
        i += 1
        time.sleep(0.01)  # ~100 Hz


def _wait_for_data(api_server: ApiServer, timeout: float = 5.0) -> bool:
    """Wait until /health shows stream data."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{api_server.address[1]}"
    while time.monotonic() < deadline:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url + "/health", timeout=2) as resp:
                health = json.loads(resp.read().decode())
            if health.get("ok") and health.get("active_streams", 0) > 0:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


# ── tests ──────────────────────────────────────────────────────────────────

class TestE2EValidation:
    """Full e2e validation against a live in-process service.

    Marked ``slow`` because the full check set takes 20-30 s.
    """

    @pytest.fixture(autouse=True)
    def _service(self, tmp_path: Path):
        service = _make_service(tmp_path)
        service.start()
        port = _find_free_port()
        api = ApiServer(service, host="127.0.0.1", port=port)
        api.start()

        feed_stop = threading.Event()
        feed_thread = threading.Thread(
            target=_continuously_feed, args=(service, feed_stop, 100),
            daemon=True,
        )
        feed_thread.start()

        try:
            # Feed initial burst + wait for data to propagate
            time.sleep(0.5)
            assert _wait_for_data(api, timeout=5.0), "Service did not publish data"

            yield {
                "service": service,
                "api": api,
                "port": port,
                "tmp_path": tmp_path,
                "_feed_stop": feed_stop,
            }
        finally:
            api.stop()
            service.stop()
            feed_stop.set()

    def _url(self, fixture: dict) -> str:
        return f"http://127.0.0.1:{fixture['port']}/"

    def test_e2e_all_checks(self, _service: dict, tmp_path: Path):
        """Run all e2e checks against the in-process service."""
        from ground_station.service import e2e_validate

        url = self._url(_service)
        out_file = str(tmp_path / "e2e-report.md")

        exit_code = e2e_validate.main([
            "--url", url,
            "--out", out_file,
            "--skip", "ui",  # skip Playwright in automated test
        ])

        # Read and display the report
        md = Path(out_file).read_text(encoding="utf-8")
        print(f"\n--- E2E Report ---\n{md}")

        # All non-skip checks should pass (exit 0)
        assert exit_code == 0, f"e2e_validate exited {exit_code}"

    def test_e2e_individual_checks(self, _service: dict):
        """Run each check individually to see detailed results."""
        from ground_station.service import e2e_validate

        url = self._url(_service)
        results: list[e2e_validate.CheckResult] = []

        # health
        e2e_validate.check_health(url, results)
        assert results[-1].pass_, f"health failed: {results[-1].detail}"

        # streaming
        e2e_validate.check_streaming(url, results)
        assert results[-1].pass_, f"streaming failed: {results[-1].detail}"

        # REC (recording)
        e2e_validate.check_rec(url, results)
        assert results[-1].pass_, f"rec failed: {results[-1].detail}"

        # flight-test
        e2e_validate.check_flight_test(url, results)
        assert results[-1].pass_, f"flight-test failed: {results[-1].detail}"

        # reports
        e2e_validate.check_reports(url, results)
        assert results[-1].pass_, f"reports failed: {results[-1].detail}"

        # replay
        e2e_validate.check_replay(url, results)
        assert results[-1].pass_, f"replay failed: {results[-1].detail}"

        # presets
        e2e_validate.check_presets(url, results)
        assert results[-1].pass_, f"presets failed: {results[-1].detail}"

        print(f"\n--- Individual check results ---")
        for r in results:
            status = "SKIP" if r.skip else ("PASS" if r.pass_ else "FAIL")
            print(f"  {status} {r.check}: {r.detail}")


class TestE2EMinimal:
    """Quick sanity check: just health + state.  Runs < 2 s."""

    def _url(self, fixture: dict) -> str:
        return f"http://127.0.0.1:{fixture['port']}/"

    @pytest.fixture(autouse=True)
    def _service(self, tmp_path: Path):
        service = _make_service(tmp_path)
        service.start()
        port = _find_free_port()
        api = ApiServer(service, host="127.0.0.1", port=port)
        api.start()

        try:
            _feed_simulated_data(service, count=20)
            time.sleep(0.5)
            assert _wait_for_data(api, timeout=5.0)
            yield {"service": service, "api": api, "port": port}
        finally:
            api.stop()
            service.stop()

    def test_e2e_health_only(self, _service: dict):
        from ground_station.service import e2e_validate
        url = self._url(_service)
        results: list[e2e_validate.CheckResult] = []
        e2e_validate.check_health(url, results)
        assert results[-1].pass_, f"health: {results[-1].detail}"
