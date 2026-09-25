"""E2E validation script for dashboard."""
import os
import sys
import time
import json
import urllib.request
from playwright.sync_api import sync_playwright

def get_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18081/")
    args = ap.parse_args()
    url = args.url.rstrip("/") + "/"
    
    out_dir = os.path.expanduser("~/validation/t23")
    os.makedirs(out_dir, exist_ok=True)
    print(f"Starting E2E validation against {url}")
    
    h_before = get_json(url + 'health')
    rss_before = h_before.get('process_rss_mb') if h_before else None
    
    errs = []
    bad_reqs = []
    
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        
        pg.on("console", lambda m: errs.append(f"[{m.type}] {m.text[:200]}") if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errs.append(f"[pageerror] {str(e)[:300]}"))
        pg.on("response", lambda r: bad_reqs.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None)
        
        pg.goto(url)
        print("Waiting 15s for UI to load plugins sequentially...")
        time.sleep(15)
        
        tabs = pg.eval_on_selector_all(".ws-tab", "els=>els.map(e=>e.textContent.trim())")
        print(f"TABS: {tabs}")
        
        panel_map = {}
        for t in tabs:
            t0 = time.time()
            pg.locator(".ws-tab", has_text=t).first.click()
            time.sleep(1)
            t_render = time.time() - t0
            
            pg.screenshot(path=os.path.join(out_dir, f"{t}.png"))
            panels = pg.evaluate("Array.from(document.querySelectorAll('.plugin-card')).filter(e=>e.offsetParent!==null).map(e=>e.getAttribute('data-testid') || e.id)")
            print(f"Tab {t}: render {t_render:.2f}s, panels: {panels}")
            for panel in panels:
                panel_map.setdefault(panel, []).append(t)
                
        print("Duplicates:", {k: v for k, v in panel_map.items() if len(v) > 1})
        
        # Flight test UI
        flight_tab = [t for t, p in panel_map.items() if 'panel-experiment-runtime' == t]
        if flight_tab:
            pg.locator(".ws-tab", has_text=flight_tab[0]).first.click()
            time.sleep(1)
        
        # We don't automate the recording in this quick script, see scratch_dryruns.py
        
        b.close()
        
    s1 = get_json(url + 'state')
    time.sleep(10)
    s2 = get_json(url + 'state')
    
    h_after = get_json(url + 'health')
    rss_after = h_after.get('process_rss_mb') if h_after else None

    print(f"RSS Before: {rss_before}, After: {rss_after}")
    print("Done.")

if __name__ == '__main__':
    main()
