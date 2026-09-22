"""Standalone VPN Monitor and Auto-Router for Clash Verge.

Runs permanently in the background. Tests internet connectivity through the proxy.
If it fails, it tests latency to Japan/Singapore nodes in the active selector group,
switches to the fastest one, and drops stale connections.
"""
import time
import json
import re
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOG_PATH = Path.home() / ".vpn_monitor.log"
PIPE = "//./pipe/verge-mihomo"
PROXY = "http://127.0.0.1:7897"

CHECK_INTERVAL_S = 60
FAILS_BEFORE_HEAL = 2
NODE_PAT = re.compile(r"JP|Japan|日本|Tokyo|东京|SG|Singapore|新加坡|狮城", re.I)
DELAY_URL = "https://www.gstatic.com/generate_204"

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

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
    return sel[0] if sel else "GLOBAL", px

def node_delay(name):
    q = urllib.parse.urlencode({"timeout": 3000, "url": DELAY_URL})
    try:
        st, d = clash("GET", f"/proxies/{urllib.parse.quote(name)}/delay?{q}", timeout_s=8)
        return name, (d or {}).get("delay") if st == 200 else None
    except Exception:
        return name, None

def heal(apply=True):
    try:
        group, px = active_group()
    except Exception as e:
        log(f"heal: failed to get active group: {e}")
        return False
        
    now = px[group].get("now")
    cands = [n for n in px[group].get("all", []) if NODE_PAT.search(n)]
    
    if not cands:
        log(f"heal: no candidates found matching Japan/Singapore in group {group}")
        return False
        
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
    import sys
    if "--dry-run" in sys.argv:
        log(f"dry-run: checking net_ok...")
        ok = net_ok()
        log(f"dry-run: net_ok={ok}")
        log("dry-run: testing heal (apply=False)...")
        heal(apply=False)
        return

    log(f"VPN monitor started. Checking every {CHECK_INTERVAL_S}s.")
    fails = 0
    while True:
        try:
            ok = net_ok()
            if ok:
                fails = 0
            else:
                fails += 1
                
            if fails >= FAILS_BEFORE_HEAL:
                log(f"net: down x{fails}, healing proxy")
                if heal(apply=True):
                    fails = 0
        except Exception as e:
            log(f"monitor error: {e}")
            
        time.sleep(CHECK_INTERVAL_S)

if __name__ == "__main__":
    main()
