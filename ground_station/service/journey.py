"""Headless, read-only journey runner for the dashboard (WP6).

A journey is *declarative data*: a JSON document listing steps, each naming
a page to visit and what must be visible on it. There are no hand-written
Playwright calls in a journey::

    {
      "name": "shell smoke",
      "fixture": "docs/dashboard-platform/fixtures/S1-synthetic-replay.json",
      "base_url": "http://127.0.0.1:8081/",
      "fail_on_console_errors": true,
      "steps": [
        {
          "name": "overview shell",
          "page": "/",
          "tab": "overview",
          "assert_text": ["UAV Ground Station", "Overview"],
          "assert_testid": ["tab-overview"],
          "forbid_text": ["NaN", "undefined"]
        }
      ]
    }

Every assertion is evaluated and recorded; the run fails (nonzero exit)
when any assertion fails or any console/HTTP error is observed on a step.
The failure records the step name, the expectation and what was found.

Artifacts written to ``--out``:

* ``report.json`` — per-step verdicts, assertions with expected/actual,
  console and HTTP errors;
* ``console-errors.json`` — every console error/warning/pageerror seen;
* ``step-NN-<name>.png`` — one screenshot per step;
* ``diagnostics-bundle.json`` — the bundle fetched with
  ``GET /api/diagnostics/bundle`` with its ``browser`` section filled in
  from what this run actually observed.

Read-only by construction: the only HTTP request the runner makes is
``GET /api/diagnostics/bundle``. It never performs POST — the live service
on port 8081 can command hardware, and POSTs to it are forbidden here.
The only clicks permitted are workspace tabs and replay session rows
(``.ws-tab``, ``[data-testid="tab-..."]``, ``.rp-session-item``); a
journey naming any other click target is rejected.

    NO_PROXY=127.0.0.1,localhost python -m ground_station.service.journey \
        --journey docs/dashboard-platform/journeys/example.json \
        --out logs/journey [--url http://127.0.0.1:8081/] [--settle 4]

The core (model, evaluator, verdict, artifact writer) is driver- and
fetcher-injected, so it unit-tests with no browser and no service via
:func:`run_journey`. Playwright is imported lazily inside
:class:`PlaywrightDriver` only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlparse

# ── Declarative journey model ─────────────────────────────────────────────

# Click targets that cannot command hardware: workspace tabs switch panels
# and replay rows only fetch records (GET). Anything else is refused so a
# journey can never click a command/arm/export button against live hardware.
_ALLOWED_CLICK = (
    re.compile(r"^\.ws-tab(?:[:\[]|\s|$)"),
    re.compile(r"^\[data-testid\s*=\s*[\"']tab-[\w -]+[\"']\]$"),
    re.compile(r"^\.rp-session-item(?:[:\[]|\s|$)"),
)

DEFAULT_SETTLE_SECONDS = 4.0
_SNIPPET_CHARS = 400


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text)).strip("-").lower()
    return s[:40] or "step"


def _snippet(text: str) -> str:
    """Collapse page text to a stable, bounded 'what was found' string."""
    return re.sub(r"\s+", " ", text or "").strip()[:_SNIPPET_CHARS]


def check_click_selector(selector: str) -> None:
    """Raise unless ``selector`` is a read-only click target."""
    s = selector.strip()
    if any(rx.match(s) for rx in _ALLOWED_CLICK):
        return
    raise ValueError(
        "refusing click %r: read-only runner may click only workspace tabs "
        "(.ws-tab, [data-testid=\"tab-...\"]) and replay rows "
        "(.rp-session-item)" % selector)


def load_journey(path: str | os.PathLike) -> dict:
    """Load and validate a journey JSON file. Raises ValueError on bad data."""
    with open(path, "r", encoding="utf-8") as fh:
        journey = json.load(fh)
    validate_journey(journey)
    return journey


def validate_journey(journey: Any) -> None:
    if not isinstance(journey, dict):
        raise ValueError("journey must be a JSON object")
    steps = journey.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("journey.steps must be a non-empty list")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError("step %d must be an object" % i)
        if not step.get("name"):
            raise ValueError("step %d is missing 'name'" % i)
        if "assert_text" in step and not isinstance(
                step["assert_text"], (str, list)):
            raise ValueError("step %r assert_text must be a string or list"
                             % step["name"])
        if "assert_testid" in step and not isinstance(
                step["assert_testid"], (str, list)):
            raise ValueError("step %r assert_testid must be a string or list"
                             % step["name"])
        if "forbid_text" in step and not isinstance(
                step["forbid_text"], (str, list)):
            raise ValueError("step %r forbid_text must be a string or list"
                             % step["name"])
        for selector in _as_list(step.get("click")):
            check_click_selector(selector)
        if step.get("tab"):
            check_click_selector('[data-testid="tab-%s"]' % step["tab"])


def default_journey() -> dict:
    """Built-in read-only shell smoke used when --journey is omitted."""
    return {
        "name": "default-shell-smoke",
        "fail_on_console_errors": True,
        "steps": [
            {
                "name": "shell loads with all workspace tabs",
                "page": "/",
                "assert_text": [
                    "UAV Ground Station", "Overview", "Control", "Estimator",
                    "MRAC", "Telemetry", "Experiments", "Paths", "Bench",
                    "Replay", "Diagnostics",
                ],
                "assert_testid": [
                    "tab-overview", "tab-control", "tab-estimator",
                    "tab-mrac", "tab-telemetry", "tab-experiments",
                    "tab-paths", "tab-bench", "tab-replay", "tab-diagnostics",
                ],
            },
        ],
    }


# ── Injected driver / fetcher protocols ───────────────────────────────────

class PageDriver(Protocol):
    """A browser session. :class:`PlaywrightDriver` is the real implementation;
    tests inject a fake. The driver navigates with GET only — it exposes no
    method capable of HTTP POST."""

    def __enter__(self) -> "PageDriver": ...
    def __exit__(self, *exc: Any) -> None: ...
    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def wait(self, seconds: float) -> None: ...
    def visible_text(self) -> str: ...
    def is_visible(self, selector: str) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def console_messages(self) -> list[str]: ...
    def http_errors(self) -> list[str]: ...


# A fetcher performs exactly one GET of a JSON URL. The default uses
# urllib's GET; tests inject a recording fake. There is deliberately no
# method/parameter for POST: POSTing to the live service could command the
# drone, so the runner is structurally incapable of it.
JsonFetcher = Callable[[str], dict]


# Loopback GETs must bypass any workstation proxy (the Clash proxy on this
# box answers 127.0.0.1 requests with a flat 502). An empty ProxyHandler
# makes urllib ignore HTTP_PROXY/http_proxy for every request this opener
# makes; the runner only ever GETs the local service.
_NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}))


def default_fetch_json(url: str, timeout: float = 10.0) -> dict:
    """Fetch ``url`` with a plain, proxy-free GET and decode JSON."""
    with _NO_PROXY_OPENER.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Assertion evaluation ──────────────────────────────────────────────────

def _evaluate_step(step: dict, driver: PageDriver, settle: float,
                   console_fatal: bool = True) -> dict:
    """Navigate one step and evaluate every assertion. Always returns a
    result dict (exceptions are recorded, not raised, so later steps run)."""
    name = step["name"]
    result: dict[str, Any] = {
        "name": name,
        "url": step["_url"],
        "verdict": "pass",
        "assertions": [],
        "failures": [],
        "console_errors": [],
        "http_errors": [],
        "screenshot": step["_screenshot"],
        "error": None,
    }
    console_before = len(driver.console_messages())
    http_before = len(driver.http_errors())

    def fail(kind: str, expected: str, actual: str) -> None:
        entry = {"kind": kind, "expected": expected, "passed": False,
                 "actual": actual}
        result["assertions"].append(entry)
        result["failures"].append(entry)
        result["verdict"] = "fail"

    try:
        driver.navigate(step["_url"])
        driver.wait(settle)
        # tab shorthand, then any extra whitelisted clicks; settle after each
        if step.get("tab"):
            driver.click('[data-testid="tab-%s"]' % step["tab"])
            driver.wait(settle)
        for selector in _as_list(step.get("click")):
            check_click_selector(selector)
            driver.click(selector)
            driver.wait(settle)

        text = driver.visible_text()
        for expected in _as_list(step.get("assert_text")):
            if str(expected) in text:
                result["assertions"].append(
                    {"kind": "text", "expected": str(expected), "passed": True})
            else:
                fail("text", str(expected),
                     "text not visible. visible text was: " + _snippet(text))
        for testid in _as_list(step.get("assert_testid")):
            selector = '[data-testid="%s"]' % testid
            try:
                visible = driver.is_visible(selector)
            except Exception as exc:  # driver reports selector errors
                visible = False
                selector_note = " (%s)" % exc
            else:
                selector_note = ""
            if visible:
                result["assertions"].append(
                    {"kind": "testid", "expected": selector, "passed": True})
            else:
                fail("testid", selector,
                     "no visible element matched %s%s"
                     % (selector, selector_note))
        for forbidden in _as_list(step.get("forbid_text")):
            if str(forbidden) in text:
                fail("forbid_text", str(forbidden),
                     "forbidden text was visible: " + _snippet(text))
            else:
                result["assertions"].append(
                    {"kind": "forbid_text", "expected": str(forbidden),
                     "passed": True})
    except Exception as exc:
        result["verdict"] = "fail"
        result["error"] = "%s: %s" % (type(exc).__name__, exc)

    # Screenshot best-effort even when navigation/assertions failed.
    try:
        driver.screenshot(step["_screenshot_path"])
    except Exception as exc:
        result["screenshot"] = None
        if result["error"] is None:
            result["error"] = "screenshot failed: %s: %s" % (
                type(exc).__name__, exc)

    console_delta = driver.console_messages()[console_before:]
    http_delta = driver.http_errors()[http_before:]
    result["console_errors"] = console_delta
    result["http_errors"] = http_delta
    if (console_delta or http_delta) and console_fatal and not step.get(
            "allow_console_errors", False):
        result["verdict"] = "fail"
    return result


# ── Bundle filling / artifact writing ─────────────────────────────────────

def _browser_observation(report: dict, fixture: str | None) -> dict:
    return {
        "source_confirmed": "replayed" if fixture else "live_observed",
        "observed_by": "ground_station.service.journey",
        "journey": report["journey"],
        "fixture": fixture,
        "base_url": report["base_url"],
        "verdict": report["verdict"],
        "steps": [
            {"index": s["index"], "name": s["name"], "url": s["url"],
             "verdict": s["verdict"]}
            for s in report["steps"]
        ],
        "screenshot": report["screenshots"][0] if report["screenshots"]
        else None,
        "screenshots": report["screenshots"],
        "console_errors": report["console_errors"],
        "http_errors": report["http_errors"],
        "report": "report.json",
        "unavailable": [],
    }


def _fill_bundle(bundle: dict, report: dict, fixture: str | None) -> dict:
    """Replace the service's placeholder browser section with what this run
    observed, and move 'browser' in the evidence summary to match."""
    browser = dict(bundle.get("browser") or {})
    observed = _browser_observation(report, fixture)
    browser.update(observed)
    bundle["browser"] = browser

    summary = bundle.setdefault("evidence_summary", {})
    for bucket in ("source_confirmed", "live_observed", "replayed",
                   "unverified"):
        items = summary.setdefault(bucket, [])
        if "browser" in items:
            items.remove("browser")
    summary.setdefault(observed["source_confirmed"], []).append("browser")
    return bundle


def _write_artifacts(out_dir: Path, report: dict, bundle_doc: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    with open(out_dir / "console-errors.json", "w", encoding="utf-8") as fh:
        json.dump({"console_errors": report["console_errors"],
                   "http_errors": report["http_errors"]},
                  fh, indent=2, sort_keys=True)
    with open(out_dir / "diagnostics-bundle.json", "w",
              encoding="utf-8") as fh:
        json.dump(bundle_doc, fh, indent=2, sort_keys=True)


# ── Runner core ───────────────────────────────────────────────────────────

def run_journey(journey: dict, driver: PageDriver, *, out_dir: str | os.PathLike,
                base_url: str | None = None, settle: float = DEFAULT_SETTLE_SECONDS,
                fetcher: JsonFetcher | None = None) -> dict:
    """Execute ``journey`` against ``driver`` and write the artifact bundle.

    ``driver`` and ``fetcher`` are injected, so this needs no browser and no
    running service. Returns the report dict (also written to
    ``<out_dir>/report.json``). The runner performs GET only.
    """
    validate_journey(journey)
    if fetcher is None:
        fetcher = default_fetch_json
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = base_url or journey.get("base_url") or "http://127.0.0.1:8081/"
    fixture = journey.get("fixture")
    started_ns = time.time_ns()

    steps: list[dict] = []
    console_fatal = bool(journey.get("fail_on_console_errors", True))
    for i, step in enumerate(journey["steps"]):
        url = urljoin(base, str(step.get("page", "/")))
        shot_name = step.get("screenshot") or "step-%02d-%s.png" % (
            i, _slug(step["name"]))
        prepared = dict(step)
        prepared["_url"] = url
        prepared["_screenshot"] = shot_name
        prepared["_screenshot_path"] = str(out / shot_name)
        res = _evaluate_step(prepared, driver, settle, console_fatal)
        res["screenshot"] = shot_name if (out / shot_name).is_file() else None
        res["index"] = i
        steps.append(res)

    finished_ns = time.time_ns()

    def _dedup(seq: list) -> list:
        return list(dict.fromkeys(seq))

    all_console = _dedup([e for s in steps for e in s["console_errors"]])
    all_http = _dedup([e for s in steps for e in s["http_errors"]])
    screenshots = [s["screenshot"] for s in steps if s["screenshot"]]
    failed = [s for s in steps if s["verdict"] != "pass"]

    # Diagnostics bundle: one GET, best effort. The service may be absent
    # (fixture/offline runs); record that instead of crashing.
    bundle_url = urljoin(base, "/api/diagnostics/bundle")
    bundle_error: str | None = None
    try:
        fetched = fetcher(bundle_url)
        if not isinstance(fetched, dict):
            raise ValueError("bundle endpoint returned %s, expected object"
                             % type(fetched).__name__)
        bundle_doc = fetched
        bundle_fetched = True
    except Exception as exc:
        bundle_doc = {}
        bundle_error = "%s: %s" % (type(exc).__name__, exc)
        bundle_fetched = False

    report: dict[str, Any] = {
        "journey": journey.get("name", "unnamed journey"),
        "fixture": fixture,
        "base_url": base,
        "bundle_url": bundle_url,
        "started_ns": started_ns,
        "finished_ns": finished_ns,
        "verdict": "fail" if failed else "pass",
        "steps": steps,
        "console_errors": all_console,
        "http_errors": all_http,
        "screenshots": screenshots,
        "counts": {"steps": len(steps),
                   "passed": len(steps) - len(failed),
                   "failed": len(failed)},
        "bundle": {"path": "diagnostics-bundle.json",
                   "fetched": bundle_fetched,
                   "error": bundle_error},
    }

    browser_observed = _browser_observation(report, fixture)
    if bundle_fetched:
        _fill_bundle(bundle_doc, report, fixture)
    else:
        # No service bundle available: emit what we observed, clearly marked.
        bundle_doc = {
            "error": "GET %s failed: %s" % (bundle_url, bundle_error),
            "unavailable": "service diagnostics bundle; browser section "
                           "contains the journey observations",
            "browser": browser_observed,
        }
    _write_artifacts(out, report, bundle_doc)
    return report


# ── Real Playwright driver (lazy import) ──────────────────────────────────

class PlaywrightDriver:
    """Chrome-backed :class:`PageDriver`. Playwright is imported in
    ``__enter__`` so importing this module never requires a browser."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None
        self._console: list[str] = []
        self._http_errors: list[str] = []

    def __enter__(self) -> "PlaywrightDriver":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            channel="chrome", headless=True, args=["--no-proxy-server"])
        page = self._browser.new_page(
            viewport={"width": 1600, "height": 1000})
        page.on(
            "console",
            lambda m: self._console.append("[%s] %s" % (m.type, m.text[:200]))
            if m.type in ("error", "warning") else None)
        page.on("pageerror",
                lambda e: self._console.append("[pageerror] %s" % str(e)[:300]))
        page.on(
            "response",
            lambda r: self._http_errors.append(
                "%d %s %s" % (r.status, r.request.method, r.url))
            if r.status >= 400 else None)
        self._page = page
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def navigate(self, url: str) -> None:
        self._page.goto(url)

    def click(self, selector: str) -> None:
        check_click_selector(selector)
        self._page.locator(selector).first.click()

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def visible_text(self) -> str:
        return self._page.inner_text("body")

    def is_visible(self, selector: str) -> bool:
        loc = self._page.locator(selector)
        return loc.count() > 0 and loc.first.is_visible()

    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path)

    def console_messages(self) -> list[str]:
        return list(self._console)

    def http_errors(self) -> list[str]:
        return list(self._http_errors)


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8081/",
                    help="dashboard base URL; overrides journey base_url")
    ap.add_argument("--out", default="logs/journey")
    ap.add_argument("--journey", default=None,
                    help="journey JSON file; built-in shell smoke if omitted")
    ap.add_argument("--settle", type=float, default=DEFAULT_SETTLE_SECONDS,
                    help="seconds to wait after navigation/click")
    args = ap.parse_args(argv)

    journey = load_journey(args.journey) if args.journey else default_journey()
    os.makedirs(args.out, exist_ok=True)
    with PlaywrightDriver() as driver:
        report = run_journey(journey, driver, out_dir=args.out,
                             base_url=args.url, settle=args.settle)

    for step in report["steps"]:
        print("== [%s] %s (%s)%s" % (
            step["verdict"].upper(), step["name"], step["url"],
            (" -- " + step["error"]) if step["error"] else ""))
        for failure in step["failures"]:
            print("   EXPECTED %s %r" % (failure["kind"],
                                         failure["expected"]))
            print("   FOUND    %s" % failure["actual"])
        for err in step["console_errors"]:
            print("   CONSOLE  %s" % err)
        for err in step["http_errors"]:
            print("   HTTP     %s" % err)
    print("JOURNEY %s: %d/%d steps passed, %d console errors, %d HTTP errors"
          % (report["verdict"].upper(), report["counts"]["passed"],
             report["counts"]["steps"], len(report["console_errors"]),
             len(report["http_errors"])))
    if not report["bundle"]["fetched"]:
        print("BUNDLE FETCH FAILED: %s" % report["bundle"]["error"])
    print("ARTIFACTS %s" % args.out)
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
