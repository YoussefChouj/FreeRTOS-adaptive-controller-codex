"""Overnight watchdog: internet/proxy self-heal + Claude -> Windows agy handoff.

Run in its own terminal:   python .agent-ops/watchdog/watchdog.py
One diagnostic pass:        python .agent-ops/watchdog/watchdog.py --dry-run [--test-launch]

Every CHECK_S seconds:
  1. Internet: HTTPS through the Clash mixed port to api.anthropic.com (any HTTP status = up).
     Two consecutive failures -> heal: delay-test the Japan/Singapore nodes of the active
     selector over the Clash Verge named pipe, switch to the fastest live one, and close
     stale connections so Claude Code's next network retry (n/10) succeeds. A grace window
     then blocks handoff while Claude's retry backoff catches up.
  2. Claude liveness: mtime of the newest session transcript (content is never read).
  3. Workers: WSL pgrep for run-worker.sh (a busy worker is progress, not a stall).
  Handoff when: internet up, grace over, no worker, Claude idle > IDLE_MIN.
  Launches agy (Windows) in a new console with agy-handoff-prompt.md. One handoff per lock.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATE = REPO / ".agent_state"
LOG = REPO / ".agent-ops" / "logs" / "watchdog.log"
LOCK = STATE / "HANDOFF_ACTIVE"
BACK = STATE / "CLAUDE_BACK"
PROMPT = REPO / ".agent-ops" / "watchdog" / "agy-handoff-prompt.md"
DRYRUN_PROMPT = REPO / ".agent-ops" / "watchdog" / "agy-dryrun-prompt.md"
TRANSCRIPTS = Path.home() / ".claude" / "projects" / "C--Users-Acer-Desktop-UAV-lab-FreeRTOS-adaptive-controller-codex"
AGY = r"D:\Tools\agy\bin\agy.exe"
PIPE = "//./pipe/verge-mihomo"
PROXY = "http://127.0.0.1:7897"

CHECK_S = 60
FAILS_BEFORE_HEAL = 2
GRACE_AFTER_HEAL_S = 15 * 60      # Claude Code retry backoff (n/10) needs time to reconnect
IDLE_MIN = 20                     # Claude silent this long with no worker running -> handoff
NODE_PAT = re.compile(r"JP|Japan|日本|Tokyo|东京|SG|Singapore|新加坡|狮城", re.I)
DELAY_URL = "https://www.gstatic.com/generate_204"


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# VPN healing is now handled by the dedicated vpn_monitor.py background process.


# ---- probes ----
def net_ok():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    try:
        op.open("https://api.anthropic.com/", timeout=10)
        return True
    except urllib.error.HTTPError:
        return True          # any HTTP answer means the path works
    except Exception:
        return False


def claude_idle_s():
    files = list(TRANSCRIPTS.glob("*.jsonl"))
    if not files:
        return None
    return time.time() - max(f.stat().st_mtime for f in files)


def workers_running():
    try:
        r = subprocess.run(["wsl.exe", "-e", "sh", "-c", "pgrep -cf 'run-worker\\.sh [0-9]{8}-[0-9]{6}' || true"],
                           capture_output=True, text=True, timeout=20)
        return int((r.stdout or "0").strip() or 0)
    except Exception as e:
        log(f"workers: probe failed ({e}); assuming busy")
        return -1


def launch_agy(prompt_file, interactive=True):
    msg = f"Read {prompt_file.relative_to(REPO).as_posix()} and follow it exactly."
    mode = "-i" if interactive else "-p"
    cmd = f'start "agy-handoff" /D "{REPO}" "{AGY}" --dangerously-skip-permissions {mode} "{msg}"'
    subprocess.Popen(cmd, shell=True, cwd=REPO)
    log(f"launched agy ({mode}) with {prompt_file.name}")


def tick(st):
    ok = net_ok()
    st["fails"] = 0 if ok else st["fails"] + 1
    if st["fails"] >= FAILS_BEFORE_HEAL:
        log(f"net: down x{st['fails']}, waiting for vpn_monitor to heal...")
        # vpn_monitor handles the actual healing in the background.
        st["grace_until"] = time.time() + GRACE_AFTER_HEAL_S
        st["fails"] = 0
        return
    idle = claude_idle_s()
    if LOCK.exists():
        if idle is not None and idle < 120 and not BACK.exists():
            BACK.write_text(dt.datetime.now().isoformat(), encoding="utf-8")
            log("claude active again during handoff -> wrote CLAUDE_BACK")
        return
    if not ok or time.time() < st["grace_until"] or idle is None or idle < IDLE_MIN * 60:
        return
    w = workers_running()
    if w != 0:
        return
    log(f"handoff: claude idle {idle/60:.0f} min, no workers, net ok")
    LOCK.write_text(dt.datetime.now().isoformat(), encoding="utf-8")
    launch_agy(PROMPT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="one diagnostic pass, no switch, no handoff")
    ap.add_argument("--test-launch", action="store_true", help="with --dry-run: launch agy on the dry-run prompt")
    a = ap.parse_args()
    if a.dry_run:
        log(f"dry-run: net_ok={net_ok()}")
        idle = claude_idle_s()
        log(f"dry-run: claude idle={None if idle is None else round(idle/60,1)} min, workers={workers_running()}, "
            f"lock={LOCK.exists()}, would_handoff={idle is not None and idle >= IDLE_MIN*60}")
        if a.test_launch:
            launch_agy(DRYRUN_PROMPT, interactive=False)
        return
    log(f"watchdog start (check {CHECK_S}s, idle {IDLE_MIN} min, grace {GRACE_AFTER_HEAL_S//60} min)")
    st = {"fails": 0, "grace_until": 0.0}
    while True:
        try:
            tick(st)
        except Exception as e:
            log(f"tick error: {e}")
        time.sleep(CHECK_S)


if __name__ == "__main__":
    sys.exit(main())
