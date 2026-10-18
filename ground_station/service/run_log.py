"""8081 keeps its own console output in a file (lab 10-18: the service ran in its own window and nothing saved its
stdout/stderr, so the bridge's "ingest behind" lines around the two takeoff link losses were gone with the window).

``tee_console(port)`` runs once in ``__main__``, right after the single-instance guard. From then on sys.stdout and
sys.stderr also write to ``logs/service/<port>-<YYYYmmdd-HHMMSS>.log``, each line stamped with local wall-clock time
to the millisecond so it lines up with a session's telemetry.csv and events.jsonl. The window shows the same text,
unstamped; the file is written first, so a line is kept even if the console write then blocks. faulthandler writes a hard crash's tracebacks (all threads) to the same file. The newest KEEP logs per port
stay; older ones are removed when a new one opens.
"""

from __future__ import annotations

import faulthandler
import sys
import threading
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "service"
KEEP = 200  # PROPOSED: 20+ lab days at several restarts a day; a log is a few KB


def stamp(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


class Tee:
    """A text stream: writes go to ``console`` unchanged and to ``fh`` with ``stamp()`` at each line start."""

    def __init__(self, console, fh, lock, clock=time.time):
        self._console, self._fh, self._lock, self._clock = console, fh, lock, clock
        self._bol = True

    def write(self, s: str) -> int:
        with self._lock:
            out, i = [], 0
            while i < len(s):
                j = s.find("\n", i)
                end = len(s) if j < 0 else j + 1
                if self._bol:
                    out.append(stamp(self._clock()) + " ")
                out.append(s[i:end])
                self._bol = j >= 0
                i = end
            try:
                self._fh.write("".join(out))
                if "\n" in s:
                    self._fh.flush()
            except (OSError, ValueError):
                pass
        if self._console is not None:
            try:
                self._console.write(s)
            except (OSError, ValueError):
                pass                                   # a dead console must not stop the service
        return len(s)

    def flush(self) -> None:
        for f in (self._console, self._fh):
            try:
                if f is not None:
                    f.flush()
            except (OSError, ValueError):
                pass

    def __getattr__(self, name):                       # encoding, isatty, fileno, ... come from the console
        return getattr(self._console if self._console is not None else self._fh, name)


def quickedit_off() -> bool:
    """Turn QuickEdit off in this process's console window, if it has one; True when it was turned off.

    With QuickEdit on, a left click in the window starts a selection and Windows blocks every write to that console
    until the selection ends, so one click in the 8081 window freezes each thread that prints, the bridge's ingest
    thread ("ingest behind") among them. Right-click > Mark still selects text, and the log file has every line.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    # CONIN$ is the console's input buffer even when stdin is redirected; QuickEdit is one of its mode bits
    h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)   # read|write, share read|write, OPEN_EXISTING
    if h is None or h == ctypes.c_void_p(-1).value:
        return False                                   # no console: pythonw, a test run, a pipe
    try:
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(ctypes.c_void_p(h), ctypes.byref(mode)):
            return False
        ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x40, 0x80
        return bool(k32.SetConsoleMode(ctypes.c_void_p(h), (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE))
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def prune(port: int, keep: int = KEEP, log_dir: Path = LOG_DIR) -> list[Path]:
    """Remove all but the newest ``keep - 1`` logs of this port (the new one makes ``keep``); returns the removed."""
    logs = sorted(log_dir.glob(f"{port}-*.log"))      # names sort by start time
    gone = []
    for p in logs[:max(0, len(logs) - (keep - 1))]:
        try:
            p.unlink()
            gone.append(p)
        except OSError:
            pass                                       # still open in an editor: try again next start
    return gone


def tee_console(port: int, log_dir: Path = LOG_DIR, now: float | None = None) -> Path:
    """Send sys.stdout/sys.stderr to the console and to a new stamped log file; returns the file's path."""
    now = time.time() if now is None else now
    log_dir.mkdir(parents=True, exist_ok=True)
    prune(port, log_dir=log_dir)
    path = log_dir / f"{port}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}.log"
    fh = open(path, "a", encoding="utf-8", errors="replace")
    lock = threading.Lock()
    sys.stdout = Tee(sys.stdout, fh, lock)
    sys.stderr = Tee(sys.stderr, fh, lock)
    faulthandler.enable(file=fh, all_threads=True)
    return path
