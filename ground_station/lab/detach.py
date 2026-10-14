"""Start a lab process that outlives the shell that started it.

    python -m ground_station.lab.detach --log <file> [--pid <file>] -- <command> [args...]

2026-10-14 lab: 8081 and the scrcpy loop, started from Claude Code's bash (`nohup ... &`), died together at
18:51 when Claude Code reaped its background work under low memory, and the flight lost its link and camera.
Here the process gets its own hidden console and process group, and this launcher exits at once, so a kill of
the starting shell's process tree no longer reaches it. Its stdout and stderr go to --log; --pid gets the
Windows PID (check it with `tasklist //FI "PID eq <pid>"`, end it with `taskkill //F //PID <pid>`).
"""
import argparse
import shutil
import subprocess
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", required=True)
    ap.add_argument("--pid")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command after --")
    # CreateProcess searches System32 before PATH, and System32 holds WSL's bash.exe: resolve along PATH
    # (Git Bash first) the way the calling shell would.
    cmd[0] = sys.executable if cmd[0] == "python" else (shutil.which(cmd[0]) or cmd[0])
    kw = {}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0                                      # SW_HIDE: GUI windows (scrcpy, tracker) still show
        kw = dict(startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kw = dict(start_new_session=True)
    with open(a.log, "ab") as log:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kw)
    if a.pid:
        with open(a.pid, "w") as f:
            f.write(f"{p.pid}\n")
    print(p.pid)


if __name__ == "__main__":
    main()
