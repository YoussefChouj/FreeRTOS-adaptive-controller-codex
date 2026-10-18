"""8081 console log (run_log): stamped lines in the file, the console unchanged, old logs pruned."""

import io
import re
import subprocess
import sys
import threading
from pathlib import Path

from ground_station.service import run_log

ROOT = Path(__file__).resolve().parents[3]
STAMPED = re.compile(r"^\d\d:\d\d:\d\d\.\d{3} ")


def test_tee_stamps_each_file_line_and_leaves_the_console_as_written():
    con, fh = io.StringIO(), io.StringIO()
    t = run_log.Tee(con, fh, threading.Lock(), clock=lambda: 0.25)
    t.write("ingest behind: 640 ms")                   # print() writes the text, then "\n"
    t.write("\n")
    t.write("a\nb\n\nc")
    assert con.getvalue() == "ingest behind: 640 ms\na\nb\n\nc"
    lines = fh.getvalue().split("\n")
    assert [STAMPED.sub("", ln) for ln in lines] == ["ingest behind: 640 ms", "a", "b", "", "c"]
    assert all(STAMPED.match(ln) for ln in lines)       # one stamp per line, none mid-line
    assert lines[0].endswith(".250 ingest behind: 640 ms")


def test_a_dead_console_does_not_stop_the_file():
    class Dead:
        def write(self, s):
            raise OSError("window closed")
    fh = io.StringIO()
    run_log.Tee(Dead(), fh, threading.Lock()).write("still logged\n")
    assert fh.getvalue().endswith("still logged\n")


def test_prune_keeps_the_newest_logs_of_that_port_only(tmp_path):
    for i in range(5):
        (tmp_path / f"8081-20261018-19000{i}.log").write_text("x")
    (tmp_path / "8082-20261018-190000.log").write_text("x")
    gone = run_log.prune(8081, keep=3, log_dir=tmp_path)
    assert [p.name for p in gone] == ["8081-20261018-190000.log", "8081-20261018-190001.log",
                                      "8081-20261018-190002.log"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["8081-20261018-190003.log", "8081-20261018-190004.log",
                                                          "8082-20261018-190000.log"]


def test_a_real_process_logs_stdout_stderr_and_thread_crashes(tmp_path):
    """Real outcome: a child process tees, prints from the main and another thread, and a thread dies; the file
    holds every line stamped and the console still got them."""
    code = (
        "import sys, threading\n"
        "from pathlib import Path\n"
        "from ground_station.service import run_log\n"
        f"p = run_log.tee_console(8099, log_dir=Path(r'{tmp_path}'))\n"
        "print('[service] up')\n"
        "print('[wifi_bridge] ingest behind: max frame age 640 ms', file=sys.stderr, flush=True)\n"
        "def boom():\n"
        "    raise RuntimeError('ingest thread died')\n"
        "t = threading.Thread(target=boom); t.start(); t.join()\n"
        "print(p)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    logs = list(tmp_path.glob("8099-*.log"))
    assert len(logs) == 1 and str(logs[0]) in r.stdout
    text = logs[0].read_text(encoding="utf-8")
    assert re.search(r"^\d\d:\d\d:\d\d\.\d{3} \[service\] up$", text, re.M)
    assert re.search(r"^\d\d:\d\d:\d\d\.\d{3} \[wifi_bridge\] ingest behind: max frame age 640 ms$", text, re.M)
    assert "RuntimeError: ingest thread died" in text
    assert "[service] up" in r.stdout and "ingest behind" in r.stderr and "ingest thread died" in r.stderr


def test_quickedit_off_in_a_real_console_window(tmp_path):
    """Real outcome: a child in its own (hidden) console has QuickEdit on, quickedit_off() clears the bit, and the
    console's mode read back afterwards has it cleared. Without a console it is a no-op that returns False."""
    if sys.platform != "win32":
        assert run_log.quickedit_off() is False
        return
    out = tmp_path / "mode.txt"
    code = (
        "import ctypes\n"
        "from ground_station.service import run_log\n"
        "k = ctypes.WinDLL('kernel32'); k.CreateFileW.restype = ctypes.c_void_p\n"
        "def mode():\n"
        "    h = ctypes.c_void_p(k.CreateFileW('CONIN$', 0xC0000000, 3, None, 3, 0, None)); m = ctypes.c_uint32()\n"
        "    k.GetConsoleMode(h, ctypes.byref(m)); k.CloseHandle(h); return m.value\n"
        "h = ctypes.c_void_p(k.CreateFileW('CONIN$', 0xC0000000, 3, None, 3, 0, None))\n"
        "k.SetConsoleMode(h, mode() | 0x80 | 0x40); k.CloseHandle(h)\n"   # start from QuickEdit on, as a new window does
        "before = mode(); ok = run_log.quickedit_off()\n"
        f"open(r'{out}', 'w').write(f'{{before}} {{ok}} {{mode()}}')\n"
    )
    si = subprocess.STARTUPINFO()
    si.dwFlags, si.wShowWindow = subprocess.STARTF_USESHOWWINDOW, 0          # SW_HIDE: no window flashes up
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, timeout=60, startupinfo=si,
                   creationflags=subprocess.CREATE_NEW_CONSOLE, check=True)
    before, ok, after = out.read_text().split()
    assert int(before) & 0x40 and ok == "True" and not int(after) & 0x40


def test_quickedit_off_without_a_console_returns_false():
    code = "from ground_station.service import run_log; print(run_log.quickedit_off())"
    flags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                       creationflags=flags)
    assert r.stdout.strip() == "False", r.stderr
