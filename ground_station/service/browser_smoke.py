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
import os
import sys
import time


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8081/")
    ap.add_argument("--out", default="logs/browser_smoke")
    ap.add_argument("--settle", type=float, default=4.0, help="seconds per tab")
    ap.add_argument("--no-replay-detail", action="store_true",
                    help="skip clicking a replay session row (its record fetch "
                         "is large on a long session)")
    args = ap.parse_args(argv)

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
