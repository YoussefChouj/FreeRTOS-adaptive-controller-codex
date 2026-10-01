"""Standalone VPN monitor for Clash Verge (started at logon by StartVPNMonitor.bat).

Every 60 s it checks the internet through the proxy and keeps the main selector on a Japan/Singapore node.
If the net is down twice in a row, or the selector is on another region or on a group such as 自动选择,
it delay-tests the Japan/Singapore nodes (one controller request) and pins the fastest live one.

Safety rules, because other Claude sessions share the proxy at 127.0.0.1:7897:
- one instance only (named mutex): a second copy exits at once.
- it never starts, stops or restarts any process, and never touches Clash Verge's files.
- one controller request at a time, each with a hard deadline; the pipe handle is always closed.
- it never closes connections on a working node. After switching away from a dead node it closes
  only that node's connections, so their clients reconnect through the new node.
- while the core is down, and for CORE_GRACE_S after it comes back, it only watches.
"""
import ctypes
import json
import msvcrt
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

LOG_PATH = Path.home() / ".vpn_monitor.log"
LOG_MAX_BYTES = 1_000_000
PIPE_DIR = "//./pipe/"
PIPE_PREFIX = "verge-mihomo"   # name gets "-sidecar-release-<hash>" / "-production-<hash>" depending on how Verge started it
PROXY = "http://127.0.0.1:7897"

CHECK_INTERVAL_S = 60
FAILS_BEFORE_HEAL = 2
CORE_GRACE_S = 120        # after the core (re)starts, let Clash Verge restore its saved selections first
PIPE_TIMEOUT_S = 10
DELAY_TIMEOUT_MS = 5000
HEARTBEAT_S = 3600
NODE_PAT = re.compile(r"JP|Japan|日本|Tokyo|东京|SG|Singapore|新加坡|狮城", re.I)
DELAY_URL = "https://www.gstatic.com/generate_204"
GROUP_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}
MUTEX_NAME = "Local\\vpn_monitor_clash_verge"
ERROR_ALREADY_EXISTS = 183

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
k32.CreateMutexW.restype = wintypes.HANDLE
k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                              ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.PeekNamedPipe.restype = wintypes.BOOL
_mutex = None

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

def single_instance():
    """Hold a named mutex for the life of the process; False if another monitor already holds it."""
    global _mutex
    _mutex = k32.CreateMutexW(None, False, MUTEX_NAME)
    return bool(_mutex) and ctypes.get_last_error() != ERROR_ALREADY_EXISTS

def find_pipe():
    names = sorted(n for n in os.listdir(PIPE_DIR) if n.startswith(PIPE_PREFIX))
    if not names:
        raise FileNotFoundError(f"no {PIPE_PREFIX}* controller pipe")
    return PIPE_DIR + names[0]

def _complete(raw):
    head, sep, rest = raw.partition(b"\r\n\r\n")
    if not sep:
        return False
    m = re.search(rb"Content-Length: *(\d+)", head, re.I)
    if m:
        return len(rest) >= int(m.group(1))
    return b"chunked" in head.lower() and (b"\r\n" + rest).endswith(b"\r\n0\r\n\r\n")

def clash(method, path, body=None, timeout_s=PIPE_TIMEOUT_S):
    """One HTTP request over the mihomo controller pipe, with a hard deadline. The handle is always closed."""
    data = json.dumps(body).encode() if body is not None else b""
    head = (f"{method} {path} HTTP/1.1\r\nHost: clash\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode()
    f = open(find_pipe(), "r+b", buffering=0)
    try:
        f.write(head + data)
        handle = msvcrt.get_osfhandle(f.fileno())
        avail = wintypes.DWORD()
        raw = b""
        deadline = time.monotonic() + timeout_s
        while not _complete(raw):
            if not k32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None):
                break                       # mihomo closed its end: nothing more is coming
            if avail.value:
                raw += f.read(avail.value)  # only what is already there, so this never blocks
            elif time.monotonic() > deadline:
                raise TimeoutError(f"{method} {path.split('?')[0]}: no reply in {timeout_s} s")
            else:
                time.sleep(0.05)
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
    return sel[0] if sel else "GLOBAL", px

def is_preferred_node(name, px):
    """A real Japan/Singapore node, never a group such as 自动选择 (which could hold other regions)."""
    return bool(NODE_PAT.search(name or "")) and px.get(name, {}).get("type") not in GROUP_TYPES

def test_candidates(group, px):
    """Delay-test the group's Japan/Singapore nodes. mihomo tests them in parallel: one controller request."""
    cands = [n for n in px[group].get("all", []) if is_preferred_node(n, px)]
    q = urllib.parse.urlencode({"url": DELAY_URL, "timeout": DELAY_TIMEOUT_MS})
    st, d = clash("GET", f"/group/{urllib.parse.quote(group)}/delay?{q}", timeout_s=DELAY_TIMEOUT_MS / 1000 + 10)
    res = {n: (d or {}).get(n) for n in cands} if st == 200 else {}
    live = sorted((v, n) for n, v in res.items() if v)
    log(f"heal: group={group} now={px[group].get('now')} live={len(live)}/{len(cands)} best={live[:3]}")
    return live, res

def close_connections_on(node):
    """Close only the connections routed through `node`; everything else keeps running."""
    _, d = clash("GET", "/connections")
    ids = [c["id"] for c in (d or {}).get("connections") or [] if node in c.get("chains", [])]
    for cid in ids:
        clash("DELETE", f"/connections/{cid}")
    return len(ids)

def heal(apply=True):
    try:
        group, px = active_group()
        now = px[group].get("now")
        live, res = test_candidates(group, px)
    except Exception as e:
        log(f"heal: {e}")
        return False
    if not live:
        log("heal: no Japan/Singapore node answers; will retry next cycle")
        return False

    best_d, best = live[0]
    if res.get(now) and res[now] <= best_d * 1.3 + 50:
        log(f"heal: current node {now} alive ({res[now]} ms), keeping")
        return True
    if apply:
        st, _ = clash("PUT", f"/proxies/{urllib.parse.quote(group)}", {"name": best})
        log(f"heal: switched {group}: {now} -> {best} ({best_d} ms) status={st}")
        if not res.get(now) and is_preferred_node(now, px):
            log(f"heal: closed {close_connections_on(now)} connections on dead node {now}")
    return True

def net_ok():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    try:
        op.open("https://api.anthropic.com/", timeout=10)
        return True
    except urllib.error.HTTPError:
        return True          # any HTTP answer means the path works
    except Exception:
        return False

def main():
    if "--dry-run" in sys.argv:          # read-only: no switching, no closing, no mutex
        log(f"dry-run: net_ok={net_ok()}")
        heal(apply=False)
        return
    if not single_instance():
        log("another VPN monitor is already running; this copy exits")
        return

    log(f"VPN monitor started (pid {os.getpid()}). Checking every {CHECK_INTERVAL_S}s.")
    fails, core_since, last_beat, down_logged = 0, None, None, False
    while True:
        try:
            try:
                find_pipe()
            except FileNotFoundError:
                if not down_logged:
                    log("Clash Verge core is down; only watching until it is back")
                core_since, down_logged = None, True
                continue
            down_logged = False
            if core_since is None:
                core_since = time.monotonic()
                log(f"core is up; leaving the selector alone for {CORE_GRACE_S}s")
            fails = 0 if net_ok() else fails + 1
            if time.monotonic() - core_since < CORE_GRACE_S:
                continue
            group, px = active_group()
            now = px[group].get("now")
            if fails >= FAILS_BEFORE_HEAL:
                log(f"net: down x{fails}, healing proxy")
            elif not is_preferred_node(now, px):
                log(f"selector {group} is on {now}, not a Japan/Singapore node: healing")
            elif last_beat is None or time.monotonic() - last_beat > HEARTBEAT_S:
                log(f"ok: {group} on {now}")
                last_beat = time.monotonic()
            if fails >= FAILS_BEFORE_HEAL or not is_preferred_node(now, px):
                if heal(apply=True):
                    fails = 0
        except Exception as e:
            log(f"monitor error: {e}")
        finally:
            time.sleep(CHECK_INTERVAL_S)

if __name__ == "__main__":
    main()
