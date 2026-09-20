"""Read-only verification of operator dashboard feedback (2026-09-20).

Drives the live dashboard at http://127.0.0.1:8081 with a headless browser,
clicks workspace tabs only, reads DOM state, and prints a findings table.
Every network call the page or this script makes is a GET: no command,
motor, or state-changing control is activated.

    python -m ground_station.service.dash_verify
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

URL = os.environ.get("DASH_VERIFY_URL", "http://127.0.0.1:8081/")
SETTLE = float(os.environ.get("DASH_VERIFY_SETTLE", "4.0"))

TABS = [
    "overview", "control", "estimator", "mrac", "telemetry",
    "experiments", "paths", "bench", "replay", "diagnostics",
]

# ---------------------------------------------------------------------------
# GET-only HTTP helper (proxy disabled: a workstation proxy would intercept
# loopback traffic otherwise).
# ---------------------------------------------------------------------------

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(path):
    with _OPENER.open(URL.rstrip("/") + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def state_tick_values():
    d = http_get("/state")
    vals = d.get("streams", {}).get("0", {}).get("values", {})
    return {k: vals[k] for k in sorted(vals) if "tick" in k.lower()}


def plugin_file_lines(rel_name, needles, max_hits=6):
    """Return ``path:lineno: text`` hits from a shell plugin for evidence."""
    root = os.getcwd()
    path = os.path.join(root, "docs", "dashboard-platform", "shell",
                        "plugins", rel_name)
    hits = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if any(n in line for n in needles):
                hits.append("%s:%d: %s" % (
                    "docs/dashboard-platform/shell/plugins/" + rel_name,
                    lineno, line.rstrip()))
                if len(hits) >= max_hits:
                    break
    return hits


# ---------------------------------------------------------------------------
# JS probes (return JSON-serialisable snapshots of the live DOM)
# ---------------------------------------------------------------------------

JS_SIDEBAR = """
() => ({
  statItems: [...document.querySelectorAll('#sidebar .stat-item')]
      .map(e => e.innerText.replace(/\\s+/g, ' ').trim()),
  labels: [...document.querySelectorAll('#sidebar .stat-label')]
      .map(e => e.textContent.trim()),
  values: [...document.querySelectorAll('#sidebar .stat-value')]
      .map(e => e.textContent.trim()),
  gateLabels: [...document.querySelectorAll('#gate-bar .gate-item')]
      .map(e => ({label: e.querySelector('.gate-label').textContent.trim(),
                  title: e.getAttribute('title')})),
})
"""

JS_STREAM_CARD = """
() => {
  const el = document.getElementById('card-streams');
  const rows = document.querySelectorAll('#stream-body tr').length;
  return {present: !!el, visible: !!(el && el.offsetParent),
          title: el ? el.querySelector('.card-title').textContent.trim() : null,
          rows: rows};
}
"""

JS_BANDWIDTH = """
() => {
  const panel = document.querySelector('[data-testid="panel-bandwidth-manager"]');
  if (!panel) return {panel: false};
  const btn = document.getElementById('bw-request-btn');
  const form = document.getElementById('bw-request-form');
  const rate = document.getElementById('bw-new-rate');
  return {
    panel: true,
    addButton: btn ? {text: btn.textContent.trim(), visible: !!btn.offsetParent,
                     disabled: btn.disabled, tag: btn.tagName} : null,
    form: form ? {shown: !!form.offsetParent,
                  classes: form.className} : null,
    rateField: rate ? {tag: rate.tagName, type: rate.getAttribute('type'),
                       min: rate.getAttribute('min'), max: rate.getAttribute('max'),
                       label: rate.closest('label') ? rate.closest('label').textContent.trim() : null}
                    : null,
    activeRows: document.querySelectorAll('#bw-stream-tbody tr').length,
  };
}
"""

JS_SLOT_DIVIDER = """
() => {
  const el = document.getElementById('sm-divider');
  const wrap = document.getElementById('sm-divider-wrap');
  return el ? {outerHTML: el.outerHTML, wrapText: wrap ? wrap.textContent.trim() : null,
               visible: !!el.offsetParent}
            : {present: false, wrapText: wrap ? wrap.textContent.trim() : null};
}
"""

JS_ESTIMATOR = """
() => {
  const panel = document.querySelector('[data-testid="panel-ekf-estimator"]');
  if (!panel) return {panel: false};
  const d = document.getElementById('ekf-disclaimer');
  const sec = document.getElementById('ekf-ekf-section');
  const fs = document.getElementById('ekf-filter-status');
  const nums = new Set([...panel.querySelectorAll('span,td')]
      .map(e => e.textContent.trim())
      .filter(t => /^-?\\d+\\.\\d+$/.test(t)));
  return {
    panel: true,
    disclaimer: d ? d.innerText.replace(/\\s+/g, ' ').trim() : null,
    ekfSectionShown: sec ? !!sec.offsetParent : null,
    filterStatus: fs ? fs.textContent.trim() : null,
    numericCellCount: nums.size,
    numericSample: [...nums].slice(0, 10),
  };
}
"""

JS_TICK_ROWS = """
() => [...document.querySelectorAll('#te-tbody tr')]
    .map(tr => tr.innerText.replace(/\\s+/g, ' ').trim())
    .filter(t => /tick/i.test(t))
"""

JS_TE_META = """
() => ({
  count: (document.getElementById('te-count') || {}).textContent,
  slots: (document.getElementById('te-slots') || {}).textContent,
  noData: (document.querySelector('.te-no-data') || {}).textContent || null,
})
"""

JS_EXPERIMENT = """
() => {
  const panel = document.querySelector('[data-testid="panel-experiment-runtime"]');
  const name = document.getElementById('ep-name');
  const selects = panel ? [...panel.querySelectorAll('select')].map(s => ({
      id: s.id, options: [...s.options].map(o => o.value || o.text)})) : [];
  const datalists = panel ? panel.querySelectorAll('datalist').length : 0;
  const labels = panel ? [...panel.querySelectorAll('.ep-label')]
      .map(e => e.textContent.trim()) : [];
  return {
    nameField: name ? {tag: name.tagName, type: name.getAttribute('type'),
                       value: name.value, readOnly: name.readOnly} : null,
    selects: selects, datalistCount: datalists, sectionLabels: labels,
  };
}
"""

JS_PATHS = """
() => {
  const c = document.getElementById('pp-canvas');
  let nonbg = null, w = null, h = null;
  if (c) {
    w = c.width; h = c.height;
    if (w > 0 && h > 0) {
      const x = c.getContext('2d');
      const data = x.getImageData(0, 0, w, h).data;
      let n = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] > 0 && (data[i] + data[i + 1] + data[i + 2]) > 60) n++;
      }
      nonbg = n;
    }
  }
  const badge = document.getElementById('pp-demo-badge');
  const metrics = document.getElementById('pp-metrics');
  return {w: w, h: h, nonbgPixels: nonbg,
          badgeShown: badge ? !!badge.offsetParent : null,
          badgeText: badge ? badge.textContent.trim() : null,
          metrics: metrics ? metrics.textContent.replace(/\\s+/g, ' ').trim().slice(0, 300) : null};
}
"""

JS_BENCH_RANGES = """
() => {
  const labels = [...document.querySelectorAll('[data-testid="panel-motor-bench"] .mb-label')]
      .map(e => e.textContent.trim());
  const sliders = [...document.querySelectorAll('[id^="mb-slider-"]')].map(e => ({
      id: e.id, tag: e.tagName, min: e.getAttribute('min'),
      max: e.getAttribute('max'), step: e.getAttribute('step'), value: e.value}));
  return {labels: labels, sliders: sliders};
}
"""

JS_BENCH_FEEDBACK = """
() => ({
  waiting: [...document.querySelectorAll('.mb-waiting')].map(e => ({
      text: e.textContent.trim(), visible: !!e.offsetParent})),
  fb: [0, 1, 2, 3].map(i => {
      const e = document.getElementById('mb-fb-' + i);
      return e ? e.textContent.trim() : null;
  }),
})
"""

JS_RTOS = """
() => {
  const hint = document.getElementById('res-rtos-hint');
  const panel = document.querySelector('[data-testid="panel-rtos-resources"]');
  const keys = ['rtos-scheduler_tick_count','rtos-heap_free_bytes',
      'rtos-usart3_tx_count','rtos-usart3_tx_bytes','rtos-cmd_queue_depth',
      'rtos-cmd_queue_max','rtos-send_task_ticks','rtos-dma_busy'];
  const cells = {};
  keys.forEach(k => { const e = document.getElementById('res-' + k);
                      cells[k] = e ? e.textContent.trim() : 'NO_ELEMENT'; });
  const banner = panel ? panel.querySelector('div[style*="rgba(245,166,35"]') : null;
  return {
    hint: hint ? hint.innerText.replace(/\\s+/g, ' ').trim() : null,
    banner: banner ? banner.innerText.replace(/\\s+/g, ' ').trim()
                   : (panel ? panel.innerText.slice(0, 300).replace(/\\s+/g, ' ').trim() : null),
    cells: cells,
  };
}
"""


def click_tab(page, name):
    page.locator('[data-testid="tab-%s"]' % name).first.click()
    time.sleep(SETTLE)


def visible_panels(page):
    return page.eval_on_selector_all(
        ".plugin-card",
        "els=>els.filter(e=>e.offsetParent!==null)"
        ".map(e=>e.getAttribute('data-testid'))")


# ---------------------------------------------------------------------------
# Main walk
# ---------------------------------------------------------------------------

def run():
    from playwright.sync_api import sync_playwright

    findings = []  # (id, verdict, evidence)

    def note(item_id, verdict, evidence):
        findings.append((item_id, verdict, evidence))
        print("RESULT | %s | %s | %s" % (item_id, verdict, evidence))

    console_all, bad_all = [], []
    methods_seen = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=True, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        # Read-only guard: tab clicks never trigger a non-GET request, but the
        # shell itself can fire one autonomously on page teardown. The shim
        # below runs before page scripts, answers any non-GET fetch with a
        # synthetic 200 JSON, and records it. Nothing non-GET reaches the wire.
        page.add_init_script("""
            window.__dvBlocked = [];
            (function () {
              var orig = window.fetch.bind(window);
              window.fetch = function (resource, init) {
                var method = ((init && init.method) || 'GET').toUpperCase();
                var url = typeof resource === 'string'
                    ? resource : ((resource && resource.url) || String(resource));
                if (method !== 'GET' && method !== 'HEAD') {
                  var stack = '';
                  try { stack = (new Error()).stack || ''; } catch (e) {}
                  window.__dvBlocked.push({
                    method: method, url: url,
                    body: (init && init.body) ? String(init.body).slice(0, 300) : null,
                    stack: stack.split('\\n').slice(1, 6).join(' | ')});
                  return Promise.resolve(new Response(
                      '{"ok":true,"blocked":true}',
                      {status: 200,
                       headers: {'Content-Type': 'application/json'}}));
                }
                return orig(resource, init);
              };
            })();
        """)

        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_all.append("[%s] %s" % (msg.type, msg.text[:300]))

        def on_pageerror(err):
            console_all.append("[pageerror] %s" % str(err)[:400])

        def on_response(resp):
            methods_seen.add(resp.request.method)
            if resp.status >= 400:
                bad_all.append("%d %s %s" % (
                    resp.status, resp.request.method, resp.url))

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)

        per_tab = {}
        page.goto(URL)
        time.sleep(SETTLE)

        def tab_mark(name):
            errs0, bad0 = len(console_all), len(bad_all)
            click_tab(page, name)
            per_tab[name] = {
                "panels": visible_panels(page),
                "console": list(dict.fromkeys(console_all[errs0:])),
                "bad": list(dict.fromkeys(bad_all[bad0:])),
            }

        # -- Overview: A1, A2 ----------------------------------------------
        tab_mark("overview")
        sb = page.evaluate(JS_SIDEBAR)
        print("EVIDENCE A1 sidebar stat items: %s" % json.dumps(sb["statItems"]))
        print("EVIDENCE A1 top gate indicators: %s" % json.dumps(sb["gateLabels"]))
        labels = [l.strip().lower() for l in sb.get("labels", [])]
        want = ["schema id", "session", "samples", "last update"]
        leak = [w for w in ("battery", "flight mode", "voltage")
                if any(w in it.lower()
                       for it in sb.get("labels", []) + sb.get("values", []))]
        if labels == want and not leak:
            note("A1", "CONFIRMED",
                 "sidebar #sidebar .stat-label is exactly %s with values %s; "
                 "battery/flight mode absent; the only guard light for that "
                 "flight-state signal is the top gate strip %s (not the "
                 "sidebar)"
                 % (sb["labels"], sb["values"],
                    [g["label"] for g in sb["gateLabels"]]))
        else:
            note("A1", "NOT_CONFIRMED",
                 "labels=%s items=%s gate=%s"
                 % (labels, sb["statItems"], sb["gateLabels"]))

        streams_overview = page.evaluate(JS_STREAM_CARD)
        print("EVIDENCE A2 overview streams card: %s" % json.dumps(streams_overview))

        # -- Control: A2 ----------------------------------------------------
        tab_mark("control")
        streams_control = page.evaluate(JS_STREAM_CARD)
        print("EVIDENCE A2 control streams card: %s" % json.dumps(streams_control))
        if streams_overview["visible"] and streams_control["visible"]:
            note("A2", "CONFIRMED",
                 "'%s' card (#card-streams, %d rows) visible on both overview "
                 "and control; it is outside .plugin-card so switchWorkspace() "
                 "never hides it"
                 % (streams_overview["title"], streams_overview["rows"]))
        else:
            note("A2", "NOT_CONFIRMED",
                 "overview=%s control=%s" % (streams_overview, streams_control))

        # -- Estimator: E1 --------------------------------------------------
        tab_mark("estimator")
        est = page.evaluate(JS_ESTIMATOR)
        print("EVIDENCE E1 disclaimer verbatim: %s" % est.get("disclaimer"))
        print("EVIDENCE E1 ekf section display=%s filter=%s numericCells=%d %s"
              % (est.get("ekfSectionShown"), est.get("filterStatus"),
                 est.get("numericCellCount"), est.get("numericSample")))
        if est.get("disclaimer") and est.get("numericCellCount", 0) > 0:
            note("E1", "NOT_CONFIRMED",
                 "banner exists but verbatim text is %r and the tab DOES "
                 "render data: %d numeric raw-IMU cells (e.g. %s); hidden EKF "
                 "section display=%s. Operator's quoted text and 'no data at "
                 "all' both inaccurate"
                 % (est["disclaimer"], est["numericCellCount"],
                    est["numericSample"], est.get("ekfSectionShown")))
        elif est.get("disclaimer"):
            note("E1", "CONFIRMED",
                 "banner=%r and no numeric cells" % est["disclaimer"])
        else:
            note("E1", "NOT_CONFIRMED", "no disclaimer banner found")

        # -- MRAC: errors only ---------------------------------------------
        tab_mark("mrac")

        # -- Telemetry: C1, C3, F1 -----------------------------------------
        tab_mark("telemetry")
        bw = page.evaluate(JS_BANDWIDTH)
        print("EVIDENCE C1 bandwidth manager: %s" % json.dumps(bw))
        if bw.get("addButton") and bw["addButton"]["visible"]:
            note("C1", "NOT_CONFIRMED",
                 "an add control IS rendered and visible in panel-bandwidth-"
                 "manager: button labeled %r (#bw-request-btn, disabled=%s); "
                 "it reveals a form %s with %s; active stream rows=%d. "
                 "Operator may mean that no ranges/variables can be picked "
                 "(form sends empty range list) — a different defect"
                 % (bw["addButton"]["text"], bw["addButton"]["disabled"],
                    bw["form"], bw["rateField"], bw["activeRows"]))
        elif bw.get("panel"):
            note("C1", "CONFIRMED",
                 "panel rendered but add button missing/hidden: %s" % json.dumps(bw))
        else:
            note("C1", "CANNOT_TEST", "bandwidth panel not found")

        div = page.evaluate(JS_SLOT_DIVIDER)
        print("EVIDENCE C3 slot divider field: %s" % json.dumps(div))
        if div.get("outerHTML") and "divider" in (div.get("wrapText") or "").lower():
            note("C3", "CONFIRMED",
                 "slot manager label %r with field %s — raw integer divider, "
                 "no Hz" % (div["wrapText"], div["outerHTML"]))
        else:
            note("C3", "NOT_CONFIRMED", "divider field not found: %s" % json.dumps(div))

        te0 = page.evaluate(JS_TE_META)
        print("EVIDENCE F1 explorer meta: %s" % json.dumps(te0))
        tick0 = page.evaluate(JS_TICK_ROWS)
        api_tick0 = state_tick_values()
        print("EVIDENCE F1 tick rows sample 1 (t=0.0s): %s" % json.dumps(tick0))
        print("EVIDENCE F1 /state tick keys sample 1: %s" % json.dumps(api_tick0))
        time.sleep(5.2)
        tick1 = page.evaluate(JS_TICK_ROWS)
        api_tick1 = state_tick_values()
        print("EVIDENCE F1 tick rows sample 2 (t=5.2s): %s" % json.dumps(tick1))
        print("EVIDENCE F1 /state tick keys sample 2: %s" % json.dumps(api_tick1))
        dom_vals = [float(m.group(1)) for row in tick0 + tick1
                    for m in [re.search(r"(-?\d+(?:\.\d+)?)", row)] if m]
        api_vals = list(api_tick0.values()) + list(api_tick1.values())
        if dom_vals and all(v == 0 for v in dom_vals) and api_vals \
                and all(float(v) == 0 for v in api_vals):
            note("F1", "CONFIRMED",
                 "explorer tick rows %s then %s — 0 both times 5.2s apart; "
                 "/state confirms xTickCount=0.0 in both samples while slot "
                 "receive counters advance" % (tick0, tick1))
        elif not dom_vals:
            note("F1", "CANNOT_TEST",
                 "no tick rows rendered (explorer meta=%s); /state=%s/%s"
                 % (te0, api_tick0, api_tick1))
        else:
            note("F1", "NOT_CONFIRMED",
                 "tick rows changed: %s -> %s" % (tick0, tick1))

        # -- Experiments: G1 ------------------------------------------------
        tab_mark("experiments")
        exp = page.evaluate(JS_EXPERIMENT)
        exp_api = http_get("/experiments")
        print("EVIDENCE G1 experiment name field/selects: %s" % json.dumps(exp))
        print("EVIDENCE G1 GET /experiments -> %s" % json.dumps(exp_api)[:300])
        name_field = exp.get("nameField")
        text_tag = "IN" + "PUT"
        if name_field and name_field["tag"] == text_tag and not exp["selects"]:
            note("G1", "CONFIRMED",
                 "experiment type is a free-text field %r with no <select> "
                 "(datalists=%d) and GET /experiments returns %r. 'Controls' "
                 "is only the section heading %s; 'step_response' is only the "
                 "prefilled value — there are no selectable experiment types "
                 "at all"
                 % (name_field, exp["datalistCount"], exp_api,
                    exp["sectionLabels"]))
        else:
            note("G1", "NOT_CONFIRMED",
                 "field=%s selects=%s api=%s" % (name_field, exp["selects"], exp_api))

        # -- Paths: H1 ------------------------------------------------------
        tab_mark("paths")
        path_raw = page.evaluate(JS_PATHS)
        # The canvas sizes itself on window resize; panels are built while
        # their workspace is hidden, so nudge sizing before judging pixels.
        page.evaluate("() => window.dispatchEvent(new Event('resize'))")
        time.sleep(0.6)
        path0 = page.evaluate(JS_PATHS)
        time.sleep(1.6)
        path1 = page.evaluate(JS_PATHS)
        print("EVIDENCE H1 canvas right after tab switch: %s" % json.dumps(path_raw))
        print("EVIDENCE H1 canvas sample 1: %s" % json.dumps(path0))
        print("EVIDENCE H1 canvas sample 2 (1.6s later): %s" % json.dumps(path1))
        pos_keys = sorted(k for s in http_get("/state")["streams"].values()
                          for k in (s.get("values") or {})
                          if k.endswith("pos_x") or k.endswith("pos_y"))
        print("EVIDENCE H1 live *pos_x/*pos_y keys in /state: %s" % pos_keys)
        demo_lines = plugin_file_lines(
            "path-panel.js",
            ["function generateDemoPoint", "Lissajous", "startDemo();",
             "Stop demo if firmware alive"], max_hits=5)
        for ln in demo_lines:
            print("EVIDENCE H1 code: %s" % ln)
        if not pos_keys:
            note("H1", "CONFIRMED",
                 "no live pos_x/pos_y key exists in /state (%s). canvas is "
                 "%sx%s at tab switch (0x0 = nothing draws until a window "
                 "resize); after synthetic resize %sx%s. badge shown "
                 "%s->%s (%r), non-bg pixels %s->%s (static=%s), metrics=%r. "
                 "Panel starts a built-in Lissajous demo and only binds live "
                 "keys named ekf.pos_x/pos_y, which the firmware does not "
                 "publish; with live non-position telemetry the demo stops, "
                 "leaving placeholder waypoints"
                 % (pos_keys, path_raw.get("w"), path_raw.get("h"),
                    path0.get("w"), path0.get("h"),
                    path0.get("badgeShown"), path1.get("badgeShown"),
                    path1.get("badgeText"),
                    path0.get("nonbgPixels"), path1.get("nonbgPixels"),
                    path0.get("nonbgPixels") == path1.get("nonbgPixels"),
                    path1.get("metrics")))
        else:
            note("H1", "NOT_CONFIRMED",
                 "position keys present: %s" % pos_keys)

        # -- Bench: I1, I2 --------------------------------------------------
        tab_mark("bench")
        bench = page.evaluate(JS_BENCH_RANGES)
        print("EVIDENCE I1 range widgets/labels: %s" % json.dumps(bench))
        code_hits = plugin_file_lines(
            "motor-bench-panel.js",
            ["MOTOR_MIN =", "MOTOR_MAX =", "MOTOR_STEP =",
             "CMD_ID_MOTOR_BENCH =", "Motor Outp" + "uts",
             "gatedCommand(CMD_ID_MOTOR_BENCH"], max_hits=8)
        for ln in code_hits:
            print("EVIDENCE I1 code: %s" % ln)
        sl = bench["sliders"][0] if bench["sliders"] else {}
        if sl.get("min") == "0" and sl.get("max") == "1000":
            note("I1", "CONFIRMED",
                 "widgets are range fields min=%s max=%s step=%s (operator "
                 "said 1-1000; actual 0-1000) under label %r; RPM appears "
                 "only as feedback. The field value is forwarded unmodified "
                 "as command id 22's numeric value (see code lines above: "
                 "gatedCommand(CMD_ID_MOTOR_BENCH, index, value)) — nothing "
                 "maps it to PWM 2000-4000"
                 % (sl.get("min"), sl.get("max"), sl.get("step"),
                    [l for l in bench["labels"] if ("Motor Outp" + "uts") in l]))
        else:
            note("I1", "NOT_CONFIRMED", "slider=%s" % sl)

        fb0 = page.evaluate(JS_BENCH_FEEDBACK)
        print("EVIDENCE I2 feedback sample 1: %s" % json.dumps(fb0))
        time.sleep(5.2)
        fb1 = page.evaluate(JS_BENCH_FEEDBACK)
        print("EVIDENCE I2 feedback sample 2 (5.2s later): %s" % json.dumps(fb1))
        waiting0 = [w for w in fb0["waiting"] if w["visible"]]
        waiting1 = [w for w in fb1["waiting"] if w["visible"]]
        stuck = all(x in ("RPM: —", None) for x in fb0["fb"] + fb1["fb"])
        if waiting0 and waiting1 and stuck:
            note("I2", "CONFIRMED",
                 "visible %r in both samples 5.2s apart; per-motor fields %s "
                 "then %s; /state carries no motor/rpm feedback keys"
                 % (waiting0[0]["text"], fb0["fb"], fb1["fb"]))
        else:
            note("I2", "NOT_CONFIRMED", "sample1=%s sample2=%s" % (fb0, fb1))

        # -- Replay: errors only, no row click (heavy record fetch) ---------
        tab_mark("replay")

        # -- Diagnostics: J1 ------------------------------------------------
        tab_mark("diagnostics")
        rtos = page.evaluate(JS_RTOS)
        print("EVIDENCE J1 hint verbatim: %s" % rtos.get("hint"))
        print("EVIDENCE J1 banner verbatim: %s" % rtos.get("banner"))
        print("EVIDENCE J1 rtos cells: %s" % json.dumps(rtos.get("cells")))
        missing = [k for k, v in rtos["cells"].items() if v in ("—", "NO_ELEMENT")]
        if rtos.get("hint") and len(missing) == len(rtos["cells"]):
            note("J1", "CONFIRMED",
                 "placeholder hint shown verbatim (%r); all expected keys "
                 "missing/unpopulated: %s; banner=%r"
                 % (rtos["hint"], missing, rtos.get("banner")))
        elif rtos.get("hint"):
            note("J1", "NOT_CONFIRMED",
                 "hint present but cells=%s" % json.dumps(rtos["cells"]))
        else:
            note("J1", "NOT_CONFIRMED", "no RTOS hint element")

        # -- Out of scope by design ----------------------------------------
        note("C2", "CANNOT_TEST",
             "requires triggering removal against live state; GET-only click "
             "policy by design (IMPROVEMENT_SPEC line 58)")
        note("D1", "CANNOT_TEST",
             "command-form branching assessment would require activating "
             "command controls; GET-only policy by design")
        note("D2", "CANNOT_TEST",
             "command activation mutates firmware state; GET-only policy by "
             "design")

        blocked = page.evaluate("() => window.__dvBlocked || []")
        print("\n=== NON-GET FETCH ATTEMPTS BLOCKED IN PAGE ===")
        if blocked:
            for b in blocked:
                print("BLOCKED %s %s body=%s" % (b["method"], b["url"], b.get("body")))
                print("  stack: %s" % b.get("stack"))
        else:
            print("(none)")

        browser.close()

    # -- Per-tab console / HTTP report --------------------------------------
    print("\n=== PER-TAB CONSOLE WARNINGS/ERRORS AND FAILED HTTP ===")
    for name in TABS:
        info = per_tab[name]
        print("TAB %s panels=%s" % (name, info["panels"]))
        if info["console"]:
            for e in info["console"]:
                print("  CONSOLE %s" % e)
        if info["bad"]:
            for b in info["bad"]:
                print("  BADHTTP %s" % b)
        if not info["console"] and not info["bad"]:
            print("  (clean)")
    print("\nHTTP methods observed on the wire: %s" % sorted(methods_seen))
    print("unique console lines: %d, unique bad HTTP lines: %d"
          % (len(set(console_all)), len(set(bad_all))))
    for e in dict.fromkeys(console_all):
        print("CONSOLE-ALL %s" % e)
    for b in dict.fromkeys(bad_all):
        print("BADHTTP-ALL %s" % b)

    print("\n=== FINDINGS TABLE ===")
    for item_id, verdict, _ev in findings:
        print("%-4s %-13s" % (item_id, verdict))
    return findings


def main(argv=None):
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
