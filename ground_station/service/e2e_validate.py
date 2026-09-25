import os
import sys
import time
import json
import urllib.request
from collections import defaultdict
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
    
    out_dir = os.path.expanduser("~/validation/t24")
    os.makedirs(out_dir, exist_ok=True)
    print(f"Starting E2E validation against {url}")
    
    report = {
        'streaming': {},
        'panels': {},
        'errors': defaultdict(list),
        'dry_runs': [],
        'wait_time': None
    }
    
    # Pre-check active preset
    h_before = get_json(url + 'health')
    state = get_json(url + 'state')
    
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        
        current_tab = "None"
        def on_console(m):
            if m.type in ("error", "warning"):
                report['errors'][current_tab].append(f"[{m.type}] {m.text[:200]}")
        pg.on("console", on_console)
        pg.on("pageerror", lambda e: report['errors'][current_tab].append(f"[pageerror] {str(e)[:300]}"))
        
        pg.goto(url)
        
        print("Waiting for data before reading UI (up to 30s)...")
        t0_wait = time.time()
        wait_success = False
        for _ in range(300):
            try:
                sb_text = pg.locator("#sidebar").text_content(timeout=100)
                if sb_text and "NOT PUBLISHED" not in sb_text and "--" not in sb_text:
                    # Actually, if it says "NOT PUBLISHED" maybe data is arriving but the specific var is missing.
                    # The requirement says "poll until #sidebar shows a battery voltage (not --)". 
                    pass
                # But wait, battery voltage might remain NOT PUBLISHED if not in preset. 
                # Let's check for any actual value instead of --. Or just rely on what we have.
                if sb_text and "--" not in sb_text.replace("—", "--"):
                    wait_success = True
                    break
            except:
                pass
            time.sleep(0.1)
        
        t_wait = time.time() - t0_wait
        report['wait_time'] = t_wait
        if not wait_success:
            print(f"Failed to see data after {t_wait:.2f}s! (Maybe NOT PUBLISHED)")
        else:
            print(f"Data arrived after {t_wait:.2f}s")
            
        time.sleep(2) # Give a bit more time for full plugin load
        
        tabs = pg.eval_on_selector_all(".ws-tab", "els=>els.map(e=>e.textContent.trim())")
        print(f"TABS: {tabs}")
        
        panel_map = {}
        for t in tabs:
            current_tab = t
            pg.locator(".ws-tab", has_text=t).first.click()
            time.sleep(1) # wait for render
            
            # Using javascript to find VISIBLE panels
            panels = pg.evaluate("Array.from(document.querySelectorAll('.plugin-card')).filter(e=>e.offsetParent!==null).map(e=>e.getAttribute('data-testid') || e.id)")
            report['panels'][t] = panels
            for panel in panels:
                panel_map.setdefault(panel, []).append(t)
                
        report['panel_map'] = panel_map
        
        # Flight test UI
        flight_tab = [t for t, p in panel_map.items() if 'panel-experiment-runtime' in p]
        if not flight_tab:
            flight_tab = [t for t in tabs if 'flight' in t.lower() or 'experiment' in t.lower()]
        
        if flight_tab:
            current_tab = flight_tab[0]
            pg.locator(".ws-tab", has_text=current_tab).first.click()
            time.sleep(1)
            
            for run_name, controller in [('t24_pid', 'pid'), ('t24_mrac', 'mrac')]:
                print(f"Run: {run_name}")
                pg.locator('#flight-test-controller').select_option(controller)
                pg.locator('#flight-test-label').fill(run_name)
                pg.locator('#flight-test-analyse').check()
                pg.locator('#record-btn').click()
                time.sleep(15)
                pg.locator('#record-btn').click() # stop
                
                # Wait for analysis done
                t_wait = time.time()
                done = False
                while time.time() - t_wait < 180:
                    ft_state = get_json(url + 'api/flight_tests')
                    if ft_state and 'flight_tests' in ft_state:
                        runs = ft_state['flight_tests']
                        r = next((x for x in runs if x.get('label') == run_name), None)
                        if r and r.get('analysis_status') == 'done':
                            done = True
                            report['dry_runs'].append({'label': run_name, 'analysis_status': 'done', 'wait_s': time.time() - t_wait})
                            print(f"{run_name} analysis done.")
                            break
                    time.sleep(2)
                if not done:
                    report['dry_runs'].append({'label': run_name, 'analysis_status': 'timeout', 'wait_s': time.time() - t_wait})
            
        b.close()
        
    s1 = get_json(url + 'state')
    time.sleep(10)
    s2 = get_json(url + 'state')
    
    report['s1'] = s1
    report['s2'] = s2
    
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2)

    print("Done. Wrote report.json.")

if __name__ == '__main__':
    main()
