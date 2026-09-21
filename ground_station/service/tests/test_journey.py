"""Unit tests for the headless journey runner core (WP6).

No browser and no running service: a fake page driver and a fake recording
GET fetcher are injected into ``run_journey``. Nothing here listens on or
connects to port 8081.
"""
import json
import re
from pathlib import Path

import pytest

from ground_station.service import journey

# A minimal valid PNG-ish header; contents do not matter, only that the
# runner copies whatever the driver screenshotted into the artifact dir.
PNG_BYTES = b"\x89PNG\r\n\x1a\n fake-driver-screenshot"


class FakePage:
    def __init__(self, text="", testids=(), console=(), http_errors=()):
        self.text = text
        self.testids = set(testids)
        self.console = list(console)
        self.http_errors = list(http_errors)


class FakeDriver:
    """In-memory PageDriver: pages keyed by URL path."""

    def __init__(self, pages):
        self.pages = pages
        self.current = None
        self.navigated = []          # GET navigations, in order
        self.clicks = []             # selectors clicked
        self.screenshots = []        # paths written
        self._console = []
        self._http = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def navigate(self, url):
        from urllib.parse import urlparse

        path = urlparse(url).path
        if path not in self.pages:
            raise AssertionError("fake driver: no page for %s" % url)
        page = self.pages[path]
        self.current = page
        self.navigated.append(url)
        self._console.extend(page.console)
        self._http.extend(page.http_errors)

    def click(self, selector):
        journey.check_click_selector(selector)
        self.clicks.append(selector)

    def wait(self, seconds):
        pass

    def visible_text(self):
        return self.current.text

    def is_visible(self, selector):
        m = re.fullmatch(r'\[data-testid=["\']([^"\']+)["\']\]', selector)
        if not m:
            return False
        return m.group(1) in self.current.testids

    def screenshot(self, path):
        Path(path).write_bytes(PNG_BYTES)
        self.screenshots.append(path)

    def console_messages(self):
        return list(self._console)

    def http_errors(self):
        return list(self._http)


class RecordingFetcher:
    """Stand-in for the HTTP GET fetcher; records (method, url). It exposes
    no POST capability on purpose — matching the runner contract."""

    def __init__(self, bundle=None, raise_exc=None):
        self.calls = []
        self.bundle = bundle if bundle is not None else {
            "browser": {
                "source_confirmed": "unverified",
                "screenshot": "unavailable server-side",
                "console_errors": "unavailable server-side",
                "unavailable": ["screenshot", "console_errors"],
            },
            "evidence_summary": {"source_confirmed": [], "live_observed": [],
                                  "unverified": ["browser"]},
        }
        self.raise_exc = raise_exc

    def __call__(self, url):
        self.calls.append(("GET", url))
        if self.raise_exc is not None:
            raise self.raise_exc
        return json.loads(json.dumps(self.bundle))  # deep copy


BASE = "http://journey.test/"

SHELL_PAGES = {
    "/": FakePage(
        text="UAV Ground Station Overview Control Estimator MRAC Telemetry "
             "Experiments Paths Bench Replay Diagnostics",
        testids=["tab-overview", "tab-control", "tab-estimator", "tab-mrac",
                 "tab-telemetry", "tab-experiments", "tab-paths", "tab-bench",
                 "tab-replay", "tab-diagnostics"]),
    "/replay": FakePage(text="Replay session S1 loaded",
                         testids=["replay-play"]),
}


def _run(journey_doc, out, *, pages=None, fetcher=None, settle=0.0):
    driver = FakeDriver(pages if pages is not None else SHELL_PAGES)
    report = journey.run_journey(
        journey_doc, driver, out_dir=out, base_url=BASE, settle=settle,
        fetcher=fetcher or RecordingFetcher())
    return report, driver


def test_passing_journey_records_pass_verdicts(tmp_path):
    doc = {
        "name": "happy",
        "steps": [
            {"name": "shell", "page": "/",
             "assert_text": ["UAV Ground Station", "Overview"],
             "assert_testid": ["tab-overview"]},
            {"name": "replay", "page": "/replay", "tab": "replay",
             "assert_text": "S1 loaded", "assert_testid": "replay-play"},
        ],
    }
    report, driver = _run(doc, tmp_path)
    assert report["verdict"] == "pass"
    assert report["counts"] == {"steps": 2, "passed": 2, "failed": 0}
    assert [s["verdict"] for s in report["steps"]] == ["pass", "pass"]
    assert all(a["passed"] for s in report["steps"]
               for a in s["assertions"])
    assert driver.clicks == ['[data-testid="tab-replay"]']


def test_failed_text_assertion_names_step_expected_and_found(tmp_path):
    doc = {"name": "armed-check", "steps": [
        {"name": "must show armed state", "page": "/",
         "assert_text": ["MOTORS ARMED"]}]}
    report, _ = _run(doc, tmp_path / "out")
    assert report["verdict"] == "fail"
    step = report["steps"][0]
    assert step["name"] == "must show armed state"
    assert step["verdict"] == "fail"
    failure = step["failures"][0]
    assert failure["kind"] == "text"
    assert failure["expected"] == "MOTORS ARMED"
    assert "MOTORS ARMED" not in failure["actual"]
    assert "UAV Ground Station" in failure["actual"]  # what was found
    # The same detail must land on disk in report.json.
    on_disk = json.loads((tmp_path / "out" / "report.json").read_text())
    assert on_disk["steps"][0]["failures"][0]["expected"] == "MOTORS ARMED"


def test_console_error_makes_verdict_nonzero(tmp_path):
    pages = dict(SHELL_PAGES)
    pages["/replay"] = FakePage(
        text="Replay session S1 loaded", testids=["replay-play"],
        console=["[error] Uncaught boom"])
    doc = {"name": "console", "steps": [
        {"name": "shell", "page": "/", "assert_text": "UAV Ground Station"},
        {"name": "replay", "page": "/replay",
         "assert_text": "S1 loaded"}]}
    report, _ = _run(doc, tmp_path, pages=pages)
    assert report["steps"][0]["verdict"] == "pass"
    assert report["steps"][1]["verdict"] == "fail"
    assert report["steps"][1]["console_errors"] == ["[error] Uncaught boom"]
    assert report["verdict"] == "fail"
    assert report["console_errors"] == ["[error] Uncaught boom"]


def test_artifact_directory_contains_all_required_pieces(tmp_path):
    doc = {"name": "artifacts", "fixture": "fixtures/S1.json", "steps": [
        {"name": "shell shot", "page": "/",
         "assert_text": "UAV Ground Station"}]}
    out = tmp_path / "bundle-out"
    fetcher = RecordingFetcher()
    report, driver = _run(doc, out, fetcher=fetcher)
    names = {p.name for p in out.iterdir()}
    assert "report.json" in names
    assert "console-errors.json" in names
    assert "diagnostics-bundle.json" in names
    pngs = sorted(p.name for p in out.glob("*.png"))
    assert len(pngs) == 1 and pngs[0].startswith("step-00-")
    assert (out / pngs[0]).read_bytes() == PNG_BYTES

    saved = json.loads((out / "report.json").read_text())
    assert [s["verdict"] for s in saved["steps"]] == ["pass"]

    console_doc = json.loads((out / "console-errors.json").read_text())
    assert console_doc["console_errors"] == []

    bundle_doc = json.loads(
        (out / "diagnostics-bundle.json").read_text())
    browser = bundle_doc["browser"]
    assert browser["source_confirmed"] == "replayed"  # fixture declared
    assert browser["fixture"] == "fixtures/S1.json"
    assert browser["verdict"] == "pass"
    assert browser["console_errors"] == []
    assert browser["screenshots"] == report["screenshots"]
    assert browser["steps"][0]["name"] == "shell shot"
    # Browser observations replace the service-side placeholders.
    assert browser["unavailable"] == []
    assert "browser" in bundle_doc["evidence_summary"]["replayed"]
    assert "browser" not in bundle_doc["evidence_summary"]["unverified"]
    # Exactly one GET for the bundle, at the diagnostics endpoint.
    assert fetcher.calls == [
        ("GET", "http://journey.test/api/diagnostics/bundle")]


def test_runner_issues_get_only_no_post_anywhere(tmp_path):
    doc = {"name": "readonly", "steps": [
        {"name": "shell", "page": "/", "tab": "overview",
         "click": ".ws-tab", "assert_text": "UAV Ground Station"}]}
    fetcher = RecordingFetcher()
    _, driver = _run(doc, tmp_path, fetcher=fetcher)
    assert fetcher.calls and all(m == "GET" for m, _ in fetcher.calls)
    for url in driver.navigated:
        assert url.startswith("http://")  # navigation is a plain GET
    # Static guard: no POST-capable call exists in the runner source.
    src = Path(journey.__file__).read_text(encoding="utf-8")
    assert "requests.post" not in src
    assert ".post(" not in src
    assert "do_POST" not in src


def test_bundle_fetch_failure_is_recorded_but_artifacts_still_written(tmp_path):
    out = tmp_path / "nobundle"
    fetcher = RecordingFetcher(raise_exc=OSError("connection refused"))
    doc = {"name": "offline", "steps": [
        {"name": "shell", "page": "/", "assert_text": "UAV Ground Station"}]}
    report, _ = _run(doc, out, fetcher=fetcher)
    assert report["verdict"] == "pass"  # assertions held despite no service
    assert report["bundle"]["fetched"] is False
    assert "connection refused" in report["bundle"]["error"]
    bundle_doc = json.loads((out / "diagnostics-bundle.json").read_text())
    assert "failed" in bundle_doc["error"]
    assert bundle_doc["browser"]["verdict"] == "pass"
    assert (out / "report.json").is_file()
    assert next(out.glob("*.png"))


def test_missing_testid_state_assertion_fails(tmp_path):
    doc = {"name": "state", "steps": [
        {"name": "arm indicator", "page": "/",
         "assert_testid": "motors-armed"}]}
    report, _ = _run(doc, tmp_path)
    failure = report["steps"][0]["failures"][0]
    assert failure["kind"] == "testid"
    assert failure["expected"] == '[data-testid="motors-armed"]'
    assert "no visible element" in failure["actual"]
    assert report["verdict"] == "fail"


def test_journey_with_unsafe_click_target_is_rejected(tmp_path):
    doc = {"name": "evil", "steps": [
        {"name": "arm", "page": "/",
         "click": '[data-testid="command-arm"]',
         "assert_text": "UAV Ground Station"}]}
    with pytest.raises(ValueError, match="refusing click"):
        _run(doc, tmp_path)


def test_default_fetcher_bypasses_proxy_for_loopback(monkeypatch):
    # The Clash proxy on this workstation 502s loopback; the default GET
    # fetcher's opener must have no configured proxies. (build_opener treats
    # an empty ProxyHandler instance as "skip the default ProxyHandler",
    # so with no proxies configured the instance need not be retained.)
    import urllib.request
    from urllib.request import ProxyHandler

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    plain = urllib.request.build_opener()
    assert any(getattr(h, "proxies", {}).get("http") ==
               "http://proxy.example:8080"
               for h in plain.handlers if isinstance(h, ProxyHandler))
    ours = [h for h in journey._NO_PROXY_OPENER.handlers
            if isinstance(h, ProxyHandler)]
    assert all(h.proxies == {} for h in ours)
    # And the fetcher must go through that opener rather than bare urlopen
    # (which honours HTTP_PROXY/http_proxy).
    src = Path(journey.__file__).read_text(encoding="utf-8")
    assert "urllib.request.urlopen(" not in src


def test_forbid_text_fails_when_forbidden_string_is_visible(tmp_path):
    doc = {"name": "nan", "steps": [
        {"name": "no nan", "page": "/", "forbid_text": ["NaN"]}]}
    pages = {"/": FakePage(text="NaN shown", testids=["tab-overview"])}
    report, _ = _run(doc, tmp_path, pages=pages)
    assert report["verdict"] == "fail"
    failure = report["steps"][0]["failures"][0]
    assert failure["kind"] == "forbid_text"
    assert failure["expected"] == "NaN"
    assert "NaN shown" in failure["actual"]
