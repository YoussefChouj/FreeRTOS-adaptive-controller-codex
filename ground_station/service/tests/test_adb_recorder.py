"""Tests for adb_recorder module."""

import json
import logging
import pathlib
import subprocess
import time
from typing import Any, Sequence
import pytest

from ground_station.service.adb_recorder import CAMERA_ARGS, AdbRecorder


class FakeProc:
    """Fake subprocess.Popen process object recording calls."""

    def __init__(
        self,
        returncode: int = 0,
        timeout_on_first_wait: bool = False,
        terminate_error: Exception | None = None,
        kill_error: Exception | None = None,
    ) -> None:
        self.returncode: int | None = None
        self._target_returncode = returncode
        self.terminate_calls: int = 0
        self.kill_calls: int = 0
        self.wait_calls: list[float | None] = []
        self._timeout_on_first_wait = timeout_on_first_wait
        self._terminate_error = terminate_error
        self._kill_error = kill_error

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self._terminate_error is not None:
            raise self._terminate_error

    def kill(self) -> None:
        self.kill_calls += 1
        if self._kill_error is not None:
            raise self._kill_error

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self._timeout_on_first_wait and len(self.wait_calls) == 1:
            raise subprocess.TimeoutExpired(cmd="scrcpy", timeout=timeout or 0.0)
        self.returncode = self._target_returncode
        return self.returncode


class FakePopen:
    """Fake popen callable recording command and kwargs, optionally creating video file."""

    def __init__(
        self,
        proc: FakeProc | None = None,
        create_video_bytes: int | None = 1024,
        raise_oserror: bool = False,
    ) -> None:
        self._proc = proc
        self.create_video_bytes = create_video_bytes
        self.raise_oserror = raise_oserror
        self.calls: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProc:
        self.calls.append({"command": command, "kwargs": kwargs})
        if self.raise_oserror:
            raise OSError("Fake popen failed")
        proc = self._proc if self._proc is not None else FakeProc()
        if self.create_video_bytes is not None:
            for arg in command:
                if arg.startswith("--record="):
                    p = pathlib.Path(arg.split("=", 1)[1])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"x" * self.create_video_bytes)
        return proc


class FakeRunResult:
    """Fake subprocess.CompletedProcess result."""

    def __init__(self, returncode: int = 0, stdout: str = "device") -> None:
        self.returncode = returncode
        self.stdout = stdout


class FakeRun:
    """Fake subprocess.run callable returning predefined result or raising exception."""

    def __init__(
        self,
        result: FakeRunResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.result = result if result is not None else FakeRunResult()
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> FakeRunResult:
        self.calls.append({"cmd": cmd, "kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


class FakeWhich:
    """Fake shutil.which callable."""

    def __init__(self, paths: dict[str, str | None] | None = None) -> None:
        self.paths = paths if paths is not None else {"adb": "/usr/bin/adb", "scrcpy": "/usr/bin/scrcpy"}

    def __call__(self, cmd: str) -> str | None:
        return self.paths.get(cmd)


class FakeClock:
    """Fake clock returning fixed timestamp values."""

    def __init__(self, times: Sequence[float] | float = 1700000000.0) -> None:
        if isinstance(times, (int, float)):
            self.times = [float(times)]
        else:
            self.times = [float(t) for t in times]
        self.index = 0

    def __call__(self) -> float:
        if self.index < len(self.times):
            val = self.times[self.index]
            self.index += 1
            return val
        return self.times[-1]


def test_available() -> None:
    # True with both tools and state "device"
    rec_ok = AdbRecorder(
        which=FakeWhich({"adb": "/bin/adb", "scrcpy": "/bin/scrcpy"}),
        run=FakeRun(FakeRunResult(returncode=0, stdout="device\n")),
    )
    assert rec_ok.available() is True

    # False when adb is missing
    rec_no_adb = AdbRecorder(
        which=FakeWhich({"adb": None, "scrcpy": "/bin/scrcpy"}),
        run=FakeRun(FakeRunResult(returncode=0, stdout="device\n")),
    )
    assert rec_no_adb.available() is False

    # False when scrcpy is missing
    rec_no_scrcpy = AdbRecorder(
        which=FakeWhich({"adb": "/bin/adb", "scrcpy": None}),
        run=FakeRun(FakeRunResult(returncode=0, stdout="device\n")),
    )
    assert rec_no_scrcpy.available() is False

    # False for state "unauthorized"
    rec_unauth = AdbRecorder(
        which=FakeWhich({"adb": "/bin/adb", "scrcpy": "/bin/scrcpy"}),
        run=FakeRun(FakeRunResult(returncode=0, stdout="unauthorized\n")),
    )
    assert rec_unauth.available() is False

    # False for returncode 1
    rec_rc1 = AdbRecorder(
        which=FakeWhich({"adb": "/bin/adb", "scrcpy": "/bin/scrcpy"}),
        run=FakeRun(FakeRunResult(returncode=1, stdout="device\n")),
    )
    assert rec_rc1.available() is False

    # False when run raises subprocess.TimeoutExpired
    rec_timeout = AdbRecorder(
        which=FakeWhich({"adb": "/bin/adb", "scrcpy": "/bin/scrcpy"}),
        run=FakeRun(raise_exc=subprocess.TimeoutExpired(cmd=["adb", "get-state"], timeout=3.0)),
    )
    assert rec_timeout.available() is False

    # False when run raises OSError
    rec_oserr = AdbRecorder(
        which=FakeWhich({"adb": "/bin/adb", "scrcpy": "/bin/scrcpy"}),
        run=FakeRun(raise_exc=OSError("Command execution failed")),
    )
    assert rec_oserr.available() is False


def test_start_when_not_available(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    fake_popen = FakePopen()
    rec = AdbRecorder(
        which=FakeWhich({"adb": None, "scrcpy": "/bin/scrcpy"}),
        popen=fake_popen,
    )
    session_dir = tmp_path / "session_unavailable"
    caplog.set_level(logging.WARNING)

    ok1 = rec.start(session_dir)
    assert ok1 is False

    ok2 = rec.start(session_dir)
    assert ok2 is False

    assert len(fake_popen.calls) == 0
    assert not session_dir.exists()
    assert list(tmp_path.iterdir()) == []

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


def test_start_happy_path(tmp_path: pathlib.Path) -> None:
    t0 = 1700000000.0
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t0))
    expected_video_name = f"camera_{stamp}.mkv"
    expected_video_path = tmp_path / expected_video_name

    fake_proc = FakeProc()
    fake_popen = FakePopen(proc=fake_proc, create_video_bytes=2048)
    fake_which = FakeWhich()
    fake_run = FakeRun()
    fake_clock = FakeClock(t0)

    rec = AdbRecorder(
        popen=fake_popen,
        run=fake_run,
        which=fake_which,
        clock=fake_clock,
    )

    ok = rec.start(tmp_path)
    assert ok is True
    assert rec.recording is True

    assert len(fake_popen.calls) == 1
    call = fake_popen.calls[0]
    expected_command = ["scrcpy", *CAMERA_ARGS, f"--record={expected_video_path}"]
    assert call["command"] == expected_command
    assert call["kwargs"]["stdin"] == subprocess.DEVNULL
    assert call["kwargs"]["stdout"] == subprocess.DEVNULL
    assert call["kwargs"]["stderr"] == subprocess.DEVNULL

    sidecar_path = tmp_path / f"camera_{stamp}.json"
    assert sidecar_path.is_file()
    sidecar_content = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_content == {
        "command": expected_command,
        "file": expected_video_name,
        "t_start_host_s": t0,
    }


def test_start_creates_missing_nested_session_dir(tmp_path: pathlib.Path) -> None:
    nested = tmp_path / "nested" / "session" / "dir"
    assert not nested.exists()

    rec = AdbRecorder(
        popen=FakePopen(),
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock(1700000000.0),
    )
    ok = rec.start(nested)
    assert ok is True
    assert nested.is_dir()


def test_start_twice_raises_runtime_error(tmp_path: pathlib.Path) -> None:
    fake_popen = FakePopen()
    rec = AdbRecorder(
        popen=fake_popen,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock(1700000000.0),
    )
    ok = rec.start(tmp_path)
    assert ok is True
    with pytest.raises(RuntimeError):
        rec.start(tmp_path)
    assert len(fake_popen.calls) == 1


def test_popen_oserror(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    fake_popen = FakePopen(raise_oserror=True)
    rec = AdbRecorder(
        popen=fake_popen,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock(1700000000.0),
    )
    caplog.set_level(logging.WARNING)

    ok = rec.start(tmp_path)
    assert ok is False
    assert rec.recording is False

    json_files = list(tmp_path.glob("*.json"))
    assert json_files == []

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


def test_stop_happy_path(tmp_path: pathlib.Path) -> None:
    t0 = 1700000000.0
    t1 = 1700000010.0
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t0))
    expected_video_path = tmp_path / f"camera_{stamp}.mkv"

    fake_proc = FakeProc(returncode=0)
    fake_popen = FakePopen(proc=fake_proc, create_video_bytes=2048)
    rec = AdbRecorder(
        popen=fake_popen,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock([t0, t1]),
        stop_timeout_s=5.0,
    )

    rec.start(tmp_path)
    res_path = rec.stop()

    assert fake_proc.terminate_calls == 1
    assert fake_proc.wait_calls == [5.0]
    assert fake_proc.kill_calls == 0
    assert res_path == expected_video_path
    assert rec.recording is False

    sidecar_path = tmp_path / f"camera_{stamp}.json"
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert len(data) == 5
    assert data["command"] == ["scrcpy", *CAMERA_ARGS, f"--record={expected_video_path}"]
    assert data["file"] == f"camera_{stamp}.mkv"
    assert data["t_start_host_s"] == pytest.approx(t0)
    assert data["t_stop_host_s"] == pytest.approx(t1)
    assert data["returncode"] == 0


def test_stop_wait_timeout_expired(tmp_path: pathlib.Path) -> None:
    t0 = 1700000000.0
    t1 = 1700000010.0
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t0))
    expected_video_path = tmp_path / f"camera_{stamp}.mkv"

    fake_proc = FakeProc(returncode=-9, timeout_on_first_wait=True)
    fake_popen = FakePopen(proc=fake_proc, create_video_bytes=2048)
    rec = AdbRecorder(
        popen=fake_popen,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock([t0, t1]),
        stop_timeout_s=5.0,
    )

    rec.start(tmp_path)
    res_path = rec.stop()

    assert fake_proc.terminate_calls == 1
    assert fake_proc.kill_calls == 1
    assert len(fake_proc.wait_calls) == 2
    assert res_path == expected_video_path
    assert rec.recording is False


def test_stop_missing_or_empty_video(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    # Missing video file
    fake_popen_missing = FakePopen(create_video_bytes=None)
    rec1 = AdbRecorder(
        popen=fake_popen_missing,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock(1700000000.0),
    )
    rec1.start(tmp_path / "sess1")
    ret1 = rec1.stop()
    assert ret1 is None
    assert rec1.recording is False
    warnings1 = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings1) == 1

    # Empty (0-byte) video file
    caplog.clear()
    fake_popen_empty = FakePopen(create_video_bytes=0)
    rec2 = AdbRecorder(
        popen=fake_popen_empty,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock(1700000000.0),
    )
    rec2.start(tmp_path / "sess2")
    ret2 = rec2.stop()
    assert ret2 is None
    assert rec2.recording is False
    warnings2 = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings2) == 1


def test_stop_without_start(caplog: pytest.LogCaptureFixture) -> None:
    rec = AdbRecorder(
        popen=FakePopen(),
        run=FakeRun(),
        which=FakeWhich(),
    )
    caplog.set_level(logging.WARNING)
    ret = rec.stop()
    assert ret is None
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 0


def test_terminate_oserror(tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    fake_proc = FakeProc(terminate_error=OSError("No such process"))
    fake_popen = FakePopen(proc=fake_proc, create_video_bytes=100)
    rec = AdbRecorder(
        popen=fake_popen,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock(1700000000.0),
    )
    caplog.set_level(logging.WARNING)
    rec.start(tmp_path)
    ret = rec.stop()
    assert ret is not None
    assert rec.recording is False
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


def test_start_stop_start_different_stamps(tmp_path: pathlib.Path) -> None:
    t0 = 1700000000.0
    t1 = 1700000005.0
    t2 = 1700000050.0
    t3 = 1700000055.0

    fake_popen = FakePopen(create_video_bytes=512)
    rec = AdbRecorder(
        popen=fake_popen,
        run=FakeRun(),
        which=FakeWhich(),
        clock=FakeClock([t0, t1, t2, t3]),
    )

    ok1 = rec.start(tmp_path)
    assert ok1 is True
    video1 = rec.stop()
    assert video1 is not None

    ok2 = rec.start(tmp_path)
    assert ok2 is True
    video2 = rec.stop()
    assert video2 is not None

    assert video1 != video2
    assert video1.name != video2.name
    assert video1.exists()
    assert video2.exists()

    sidecars = sorted(tmp_path.glob("camera_*.json"))
    assert len(sidecars) == 2
    sc1 = json.loads(sidecars[0].read_text(encoding="utf-8"))
    sc2 = json.loads(sidecars[1].read_text(encoding="utf-8"))
    assert sc1["file"] == video1.name
    assert sc2["file"] == video2.name
    assert sc1["t_start_host_s"] == pytest.approx(t0)
    assert sc2["t_start_host_s"] == pytest.approx(t2)
