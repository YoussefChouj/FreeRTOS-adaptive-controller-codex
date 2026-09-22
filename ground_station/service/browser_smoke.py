"""Read-only browser smoke walk of the dashboard shell.

Opens every workspace tab, screenshots it, and reports console errors,
HTTP >= 400 responses and NaN/undefined text. It clicks only tabs and a
replay session row: never a command, export or play button, so it is safe
against a service connected to the drone.

    NO_PROXY=127.0.0.1,localhost python -m ground_station.service.browser_smoke \
        --out logs/smoke [--url http://127.0.0.1:8081/]

Needs Playwright (``pip install playwright``) and a Chrome install.
Exit code 1 if any console error or bad response was seen.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request


def _proxy_free_get_json(url, timeout=10.0):
    """GET ``url`` as JSON bypassing any workstation proxy (a bare
    urlopen on this box gets 502 from Clash for 127.0.0.1)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def current_head_commit():
    """Short hash of current git HEAD, or None if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def commit_mismatch_warning(health_commit, head_commit):
    """WARNING line when the service's startup commit differs from the
    current checkout (a stale service — the 2026-09-21 404 root cause).
    Returns None when the states honestly match or cannot be compared."""
    if health_commit is None:
        return ("WARNING: /health publishes no started_commit; cannot "
                "confirm the running service matches current code")
    if head_commit is None:
        return None  # no honest local basis for comparison
    if health_commit != head_commit:
        return ("WARNING: service started_commit %s differs from current "
                "HEAD %s — service may be running stale code"
                % (health_commit, head_commit))
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8081/")
    ap.add_argument("--out", default="logs/browser_smoke")
    ap.add_argument("--settle", type=float, default=4.0, help="seconds per tab")
    ap.add_argument("--no-replay-detail", action="store_true",
                    help="skip clicking a replay session row (its record fetch "
                         "is large on a long session)")
    args = ap.parse_args(argv)

    # Read-only startup-identity check before the browser walk. This GETs
    # /health (safe) and warns if the running service predates HEAD.
    try:
        health = _proxy_free_get_json(args.url.rstrip("/") + "/health")
    except OSError as exc:
        print("WARNING: cannot read /health for started_commit: %s" % exc)
    else:
        health_commit = health.get("started_commit")
        print("HEALTH started_commit=%s started_at=%s"
              % (health_commit, health.get("started_at")))
        warning = commit_mismatch_warning(health_commit, current_head_commit())
        if warning:
            print(warning)

    from playwright.sync_api import sync_playwright

    os.makedirs(args.out, exist_ok=True)
    errs, bad = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True,
                              args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("console", lambda m: errs.append("[%s] %s" % (m.type, m.text[:200]))
              if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errs.append("[pageerror] %s" % str(e)[:300]))
        pg.on("response", lambda r: bad.append("%d %s %s" % (r.status, r.request.method, r.url))
              if r.status >= 400 else None)
        pg.goto(args.url)
        time.sleep(args.settle)
        tabs = pg.eval_on_selector_all(".ws-tab", "els=>els.map(e=>e.textContent.trim())")
        print("TABS", tabs)
        if not tabs:
            print("NO TABS: shell did not load at %s" % args.url)
            b.close()
            return 1
        # Liveness gate.  This check exists because the smoke run reported
        # ERRORS 0 / BAD RESPONSES 0 on a dashboard whose every panel was
        # blank: /state answered 200 with a body containing a bare NaN, so
        # JSON.parse threw inside the shell's own poll and the rejection was
        # swallowed by a .catch().  Watching for noise cannot see that.  The
        # only reliable signal is whether live values actually reached the
        # DOM, so assert that rather than the absence of complaints.
        parse = pg.evaluate("""async () => {
          const out = [];
          for (const r of ['/state', '/api/agent/state', '/api/actions']) {
            try {
              const res = await fetch(r);
              JSON.parse(await res.text());   // the browser's parser, not ours
            } catch (e) { out.push(r + ': ' + e.message.slice(0, 120)); }
          }
          return out;
        }""")
        for p in parse:
            errs.append("[unparseable] %s" % p)
        live = pg.eval_on_selector_all(
            "#card-state .stat-value, #card-flight .stat-value",
            "els=>els.map(e=>e.textContent.trim())")
        stale = [v for v in live if v in ("", "—", "-", "NOT PUBLISHED")]
        print("STATE %d/%d sidebar values live: %s" % (
            len(live) - len(stale), len(live), live))
        if live and len(stale) == len(live):
            # Every single one still at its placeholder: the shell rendered
            # markup but never rendered data.
            errs.append("[no live state] sidebar is entirely unpopulated")

        for t in tabs:
            n0 = len(errs)
            pg.locator(".ws-tab", has_text=t).first.click()
            time.sleep(args.settle)
            pg.screenshot(path=os.path.join(args.out, "tab-%s.png" % t))
            txt = pg.inner_text("body")
            panels = pg.eval_on_selector_all(
                ".plugin-card",
                "els=>els.filter(e=>e.offsetParent!==null).map(e=>e.getAttribute('data-testid'))")
            print("== %s: errs+%d nan=%d undef=%d panels=%s" % (
                t, len(errs) - n0, txt.count("NaN"), txt.count("undefined"), panels))
        pg.locator('[data-testid="tab-replay"]').first.click()
        time.sleep(args.settle)
        if not args.no_replay_detail and pg.locator(".rp-session-item").count():
            pg.locator(".rp-session-item").first.click()
            time.sleep(args.settle)
            print("REPLAY play button visible:",
                  pg.locator('[data-testid="replay-play"]').is_visible())
            pg.screenshot(path=os.path.join(args.out, "replay-detail.png"))
        b.close()
    errs = list(dict.fromkeys(errs))
    bad = list(dict.fromkeys(bad))
    print("ERRORS %d" % len(errs))
    for e in errs:
        print("  " + e)
    print("BAD RESPONSES %d" % len(bad))
    for x in bad:
        print("  " + x)
    return 1 if (errs or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
