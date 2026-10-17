"""Tray switch for the Clash Verge exit country (started at logon by StartVPNMonitor.bat).

The profile script (clash-profile-script.js) gives each country its own auto-switching group: US-auto, JP-auto,
SG-auto and JPSG-auto. mihomo moves inside a group by itself (calm url-test), so the exit country never changes
behind your back. Two selectors pick a group:
- AI-exit    : Google (Argon, Jules, Gemini, Kaggle, Colab), Anthropic and OpenAI traffic.
- VPN07.com  : everything else.

The tray menu (right-click the icon in the hidden-icons area) shows both selectors. For each one:
- Country: USA / Japan / Singapore / Japan + Singapore.
- "Auto-switch inside the country": on = follow the country group; off = pin that group's current node and stay
  there. Japan + Singapore needs auto-switch, since one node is in one country.
Left-click pops up the current exit nodes. Icon: green = both selectors auto-switch, amber = one is pinned,
grey = problem (Clash down, Global mode, or the profile script was skipped at a subscription refresh).

Every 60 s the monitor re-reads the state, notifies once when a problem starts, and delay-tests pinned nodes so you
hear about a dead one. It never switches anything by itself: only your menu clicks change a selector.

Safety rules, because other Claude sessions share the proxy at 127.0.0.1:7897:
- one instance only (named mutex): a second copy exits at once.
- it never starts, stops or restarts any process, and never touches Clash Verge's files.
- one controller request at a time (a lock), each with a hard deadline; the pipe handle is always closed.
- it never closes connections.
Without pystray it runs headless: it only logs. `--dry-run` logs the current state once and exits.
"""
import ctypes
import json
import msvcrt
import os
import re
import sys
import threading
import time
import urllib.parse
from ctypes import wintypes
from pathlib import Path

LOG_PATH = Path.home() / ".vpn_monitor.log"
LOG_MAX_BYTES = 1_000_000
PIPE_DIR = "//./pipe/"
PIPE_PREFIX = "verge-mihomo"   # name gets "-sidecar-release-<hash>" / "-production-<hash>" depending on how Verge started it

CHECK_INTERVAL_S = 60
PIPE_TIMEOUT_S = 10
DELAY_TIMEOUT_MS = 5000
DELAY_URL = "https://www.gstatic.com/generate_204"
LANES = (("AI-exit", "AI traffic (Argon, Jules, Claude)", "AI"), ("VPN07.com", "Other traffic", "Other"))
COUNTRIES = (("US-auto", "USA"), ("JP-auto", "Japan"), ("SG-auto", "Singapore"), ("JPSG-auto", "Japan + Singapore"))
COUNTRY_NAMES = dict(COUNTRIES)
JPSG_ALIASES = {"自动选择", "故障转移"}   # the subscription's own groups; the profile script filters them to JP/SG
MUTEX_NAME = "Local\\vpn_monitor_clash_verge"
ERROR_ALREADY_EXISTS = 183

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
k32.CreateMutexW.restype = wintypes.HANDLE
k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                              ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.PeekNamedPipe.restype = wintypes.BOOL
_mutex = None
_pipe_lock = threading.Lock()

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
    """One controller request at a time: the tray and monitor threads share the pipe."""
    with _pipe_lock:
        return _clash(method, path, body, timeout_s)

def _clash(method, path, body, timeout_s):
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

def lane_state(px, lane):
    """(group, auto, node) for one selector: the country group it belongs to (None if it matches none), whether it
    follows that group's auto-switching, and the node its traffic leaves through right now."""
    now = px[lane].get("now")
    group = "JPSG-auto" if now in JPSG_ALIASES else now
    if group in COUNTRY_NAMES:
        return group, True, px.get(now, {}).get("now")
    country = next((g for g, _ in COUNTRIES[:3] if now in px.get(g, {}).get("all", [])), None)
    return country, False, now

def snapshot():
    """Read-only view of the state the tray shows. Never raises: trouble comes back as snap["problem"]."""
    try:
        _, cfg = clash("GET", "/configs")
        _, d = clash("GET", "/proxies")
    except FileNotFoundError:
        return {"problem": "Clash Verge is not running", "lanes": {}}
    except Exception as e:
        return {"problem": f"controller: {e}", "lanes": {}}
    px = d["proxies"]
    if LANES[0][0] not in px:
        return {"problem": "profile script skipped: in Clash Verge, re-activate the VPN07.com profile", "lanes": {}}
    mode = (cfg or {}).get("mode", "rule").lower()
    problem = None if mode == "rule" else f"Clash Verge is in {mode} mode: switch it to Rule, or AI-exit is bypassed"
    return {"problem": problem, "lanes": {lane: lane_state(px, lane) for lane, _, _ in LANES if lane in px}}

def describe(snap, nodes=False):
    """One entry per selector, e.g. "AI: USA auto"; with nodes=True also the exit node."""
    parts = []
    for lane, _, short in LANES:
        if lane not in snap["lanes"]:
            continue
        group, auto, node = snap["lanes"][lane]
        where = f"{COUNTRY_NAMES.get(group, 'no country')} {'auto' if auto else 'pinned'}"
        parts.append(f"{short}: {where}" + (f" -> {node}" if nodes else ""))
    return parts

def choose(lane, group, auto):
    """Point `lane` at a country group (auto) or pin the node that group is on now (not auto)."""
    _, d = clash("GET", "/proxies")
    target = group if auto else d["proxies"][group].get("now")
    if not target:
        raise RuntimeError(f"{group} has no node to pin")
    st, body = clash("PUT", f"/proxies/{urllib.parse.quote(lane)}", {"name": target})
    if st not in (200, 204):
        raise RuntimeError(f"{lane} -> {target}: HTTP {st} {(body or {}).get('message', '')}".strip())
    log(f"tray: {lane} -> {target}")

def node_alive(node):
    q = urllib.parse.urlencode({"url": DELAY_URL, "timeout": DELAY_TIMEOUT_MS})
    st, d = clash("GET", f"/proxies/{urllib.parse.quote(node)}/delay?{q}", timeout_s=DELAY_TIMEOUT_MS / 1000 + 10)
    return st == 200 and bool((d or {}).get("delay"))

def run_tray(wake):
    """Tray icon and menu in the main thread; the monitor thread feeds it snapshots.
    Returns False if pystray/Pillow is missing (the caller then runs headless)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    def image(rgb):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((4, 4, 60, 60), fill=rgb)
        return img

    images = {"auto": image((46, 160, 67)), "pinned": image((230, 160, 30)), "problem": image((140, 140, 140))}
    snap = {"problem": "starting", "lanes": {}}

    def show(new):
        """Called by the monitor thread. pystray builds the Windows menu ahead of time, so rebuild it here."""
        nonlocal snap
        snap = new
        lanes = snap["lanes"].values()
        kind = ("problem" if snap["problem"] or not lanes else
                "auto" if all(auto for _, auto, _ in lanes) else "pinned")
        title = ("VPN: " + (snap["problem"] or " | ".join(describe(snap))))[:127]   # Windows tooltip limit
        if icon.title != title:
            icon.icon, icon.title = images[kind], title
        icon.update_menu()

    def act(lane, group, auto):
        """Menu clicks run off the GUI thread: a slow controller must never freeze the tray."""
        def work():
            try:
                choose(lane, group, auto)
            except Exception as e:
                log(f"tray: {e}")
                icon.notify(str(e), "VPN switch failed")
            wake.set()                   # re-read the state at once
        threading.Thread(target=work, daemon=True).start()

    def lane_menu(lane):
        def state():
            return snap["lanes"].get(lane, (None, False, None))

        def pick(group):
            return lambda _icon, _item: act(lane, group, state()[1] or group == "JPSG-auto")

        items = [pystray.MenuItem(name, pick(group), radio=True,
                                  checked=lambda _item, g=group: state()[0] == g,
                                  enabled=lambda _item, g=group: lane in snap["lanes"] and (
                                      g != "JPSG-auto" or state()[1]))
                 for group, name in COUNTRIES]
        items += [pystray.Menu.SEPARATOR,
                  pystray.MenuItem("Auto-switch inside the country",
                                   lambda _icon, _item: act(lane, state()[0], not state()[1]),
                                   checked=lambda _item: state()[1],
                                   enabled=lambda _item: state()[0] is not None)]
        return pystray.Menu(*items)

    def status(_icon, _item):
        icon.notify("\n".join(describe(snap, nodes=True)) or snap["problem"] or "no state yet", "VPN exit")

    def quit_monitor(_icon, _item):
        log("VPN monitor quit from the tray")
        icon.stop()

    icon = pystray.Icon("vpn_monitor", images["problem"], "VPN: starting", menu=pystray.Menu(
        pystray.MenuItem("Show exit nodes", status, default=True),
        *(pystray.MenuItem(label, lane_menu(lane)) for lane, label, _ in LANES),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open log", lambda _icon, _item: os.startfile(LOG_PATH)),
        pystray.MenuItem("Quit monitor", quit_monitor)))
    threading.Thread(target=monitor_loop, args=(wake, show, icon.notify), daemon=True).start()
    icon.run()
    return True

def monitor_loop(wake, show=None, notify=None):
    """Re-read the state every CHECK_INTERVAL_S (or when woken), log changes, warn once per problem or dead pin."""
    log(f"VPN monitor started (pid {os.getpid()}). Checking every {CHECK_INTERVAL_S}s; it never switches by itself.")
    last_line, dead = None, set()
    while True:
        try:
            snap = snapshot()
            if show:
                show(snap)
            line = snap["problem"] or "ok: " + " | ".join(describe(snap, nodes=True))
            if line != last_line:
                log(line)
                if snap["problem"] and notify:
                    notify(snap["problem"], "VPN")
                last_line = line
            pinned = {node for _, auto, node in snap["lanes"].values() if not auto and node}
            for node in pinned:
                if node_alive(node):
                    dead.discard(node)
                elif node not in dead:
                    dead.add(node)
                    log(f"pinned node {node} does not answer")
                    if notify:
                        notify(f"{node} does not answer. Turn auto-switch on or pick another country.",
                               "VPN pinned node down")
            dead &= pinned
        except Exception as e:
            log(f"monitor error: {e}")
        finally:
            wake.wait(CHECK_INTERVAL_S)
            wake.clear()

def main():
    if "--dry-run" in sys.argv:          # read-only: one state read, no mutex
        snap = snapshot()
        log(f"dry-run: {snap['problem'] or 'ok'}; " + "; ".join(describe(snap, nodes=True)))
        return
    if not single_instance():
        log("another VPN monitor is already running; this copy exits")
        return
    wake = threading.Event()
    if not run_tray(wake):
        monitor_loop(wake)

if __name__ == "__main__":
    main()
