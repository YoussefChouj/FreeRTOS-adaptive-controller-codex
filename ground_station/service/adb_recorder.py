"""Phone-camera video recorder using adb and scrcpy."""

import json
import logging
import os
import pathlib
import shutil
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

# PROPOSED camera recording arguments for scrcpy
CAMERA_ARGS: tuple[str, ...] = (
    "--video-source=camera",
    "--camera-facing=back",
    "--no-audio",
    "--no-playback",
)


class AdbRecorder:
    """Manages phone-camera recording via scrcpy and adb checks."""

    def __init__(
        self,
        adb: str = "adb",
        scrcpy: str = "scrcpy",
        camera_args: tuple[str, ...] = CAMERA_ARGS,
        popen: Any = subprocess.Popen,
        run: Any = subprocess.run,
        which: Any = shutil.which,
        clock: Any = time.time,
        stop_timeout_s: float = 5.0,
    ) -> None:
        self._adb = adb
        self._scrcpy = scrcpy
        self._camera_args = camera_args
        self._popen = popen
        self._run = run
        self._which = which
        self._clock = clock
        self._stop_timeout_s = float(stop_timeout_s)

        self._process: Any = None
        self._video_path: pathlib.Path | None = None
        self._sidecar_path: pathlib.Path | None = None
        self._sidecar_data: dict[str, Any] | None = None
        self._unavailable_warned: bool = False

    def available(self) -> bool:
        """Check if adb and scrcpy are available and an Android device is attached."""
        try:
            adb_path = self._which(self._adb)
            scrcpy_path = self._which(self._scrcpy)
            if not adb_path or not scrcpy_path:
                return False
            res = self._run(
                [self._adb, "get-state"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if res.returncode != 0:
                return False
            stdout = res.stdout if isinstance(res.stdout, str) else ""
            return stdout.strip() == "device"
        except (OSError, subprocess.SubprocessError):
            return False

    @property
    def recording(self) -> bool:
        """True if recording process is active."""
        return self._process is not None

    def start(self, session_dir: str | os.PathLike) -> bool:
        """Start camera recording session."""
        if self.recording:
            raise RuntimeError("Already recording")

        if not self.available():
            if not self._unavailable_warned:
                logger.warning(
                    "adb or scrcpy unavailable or no device attached; recording not started"
                )
                self._unavailable_warned = True
            return False

        session_path = pathlib.Path(session_dir)
        session_path.mkdir(parents=True, exist_ok=True)

        t0 = self._clock()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t0))
        video_path = session_path / f"camera_{stamp}.mkv"
        command = [self._scrcpy, *self._camera_args, f"--record={video_path}"]

        try:
            proc = self._popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as err:
            logger.warning("Failed to start scrcpy process: %s", err)
            return False

        sidecar_path = session_path / f"camera_{stamp}.json"
        sidecar_data = {
            "command": command,
            "file": video_path.name,
            "t_start_host_s": t0,
        }
        sidecar_path.write_text(
            json.dumps(sidecar_data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        self._process = proc
        self._video_path = video_path
        self._sidecar_path = sidecar_path
        self._sidecar_data = sidecar_data
        return True

    def stop(self) -> pathlib.Path | None:
        """Stop camera recording session and return video path if valid."""
        if not self.recording or self._process is None:
            return None

        proc = self._process
        video_path = self._video_path
        sidecar_path = self._sidecar_path
        sidecar_data = self._sidecar_data

        self._process = None
        self._video_path = None
        self._sidecar_path = None
        self._sidecar_data = None

        try:
            proc.terminate()
        except OSError as err:
            logger.warning("Failed to terminate process: %s", err)

        try:
            proc.wait(timeout=self._stop_timeout_s)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError as err:
                logger.warning("Failed to kill process: %s", err)
            try:
                proc.wait()
            except OSError as err:
                logger.warning("Failed to wait for process: %s", err)
        except OSError as err:
            logger.warning("Failed to wait for process: %s", err)

        t_stop = self._clock()
        returncode = getattr(proc, "returncode", None)

        if sidecar_path is not None and sidecar_data is not None:
            sidecar_data["t_stop_host_s"] = t_stop
            sidecar_data["returncode"] = returncode
            try:
                sidecar_path.write_text(
                    json.dumps(sidecar_data, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            except OSError as err:
                logger.warning(
                    "Failed to update sidecar %s: %s", sidecar_path, err
                )

        if (
            video_path is not None
            and video_path.is_file()
            and video_path.stat().st_size > 0
        ):
            return video_path

        logger.warning("Video file %s missing or empty", video_path)
        return None
