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


# ---- Clash Verge controller over the named pipe (no secret needed on the pipe) ----
def clash(method, path, body=None, timeout_s=15):
    data = json.dumps(body).encode() if body is not None else b""
    head = (f"{method} {path} HTTP/1.1\r\nHost: clash\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode()
    f = open(PIPE, "r+b", buffering=0)
    try:
        f.write(head + data)
        raw = b""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            chunk = f.read(65536)
            if not chunk:
                break
            raw += chunk
            h, sep, rest = raw.partition(b"\r\n\r\n")
            if sep:
                m = re.search(rb"Content-Length: (\d+)", h, re.I)
                if m and len(rest) >= int(m.group(1)):
                    break
                if b"chunked" in h.lower() and rest.endswith(b"0\r\n\r\n"):
                    break
    finally:
        f.close()
    h, _, rest = raw.partition(b"\r\n\r\n")
    status = int(h.split(b" ")[1]) if h else 0
    if b"chunked" in h.lower():
        out = b""
        while rest:
            n, _, rest = rest.partition(b"\r\n")
            n = int(n or b"0", 16)
            if n == 0:
                break
            out += rest[:n]
            rest = rest[n + 2:]
        rest = out
    return status, (json.loads(rest) if rest.strip() else None)


def active_group():
    _, cfg = clash("GET", "/configs")
    _, px = clash("GET", "/proxies")
    px = px["proxies"]
    if (cfg or {}).get("mode", "rule").lower() == "global":
        return "GLOBAL", px
    sel = [k for k, v in px.items() if v.get("type") == "Selector" and k != "GLOBAL"]
    sel.sort(key=lambda k: -sum(bool(NODE_PAT.search(n)) for n in px[k].get("all", [])))
    return sel[0], px


def node_delay(name):
    q = urllib.parse.urlencode({"timeout": 3000, "url": DELAY_URL})
    try:
        st, d = clash("GET", f"/proxies/{urllib.parse.quote(name)}/delay?{q}", timeout_s=8)
        return name, (d or {}).get("delay") if st == 200 else None
    except Exception:
        return name, None


def heal(apply=True):
    group, px = active_group()
    now = px[group].get("now")
    cands = [n for n in px[group].get("all", []) if NODE_PAT.search(n)]
    with ThreadPoolExecutor(6) as ex:
        res = dict(ex.map(node_delay, cands))
    live = sorted((d, n) for n, d in res.items() if d)
    log(f"heal: group={group} now={now} live={len(live)}/{len(cands)} best={live[:3]}")
    if not live:
        return False
    best_d, best = live[0]
    if res.get(now) and res[now] <= best_d * 1.3 + 50:
        log(f"heal: current node {now} alive ({res[now]} ms), keeping")
        best = now
    if apply and best != now:
        st, _ = clash("PUT", f"/proxies/{urllib.parse.quote(group)}", {"name": best})
        log(f"heal: switched {group}: {now} -> {best} ({best_d} ms) status={st}")
    if apply:
        clash("DELETE", "/connections")   # drop stale sockets so clients reconnect at once
    return True


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
        log(f"net: down x{st['fails']}, healing proxy")
        try:
            if heal():
                st["grace_until"] = time.time() + GRACE_AFTER_HEAL_S
                st["fails"] = 0
        except Exception as e:
            log(f"heal: failed ({e})")
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
        heal(apply=False)
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
