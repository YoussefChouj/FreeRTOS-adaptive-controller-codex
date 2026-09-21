"""Scratch verification: failing journey through the real runner core.

Injected fake page driver (no Playwright, no service on 8081). Writes the
artifact directory like the real CLI would, then exits nonzero.
"""
import json
import sys
from pathlib import Path

from ground_station.service import journey


class DemoDriver:
    def __init__(self, text):
        self.text = text
        self.shot = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def navigate(self, url):
        pass

    def click(self, selector):
        journey.check_click_selector(selector)

    def wait(self, seconds):
        pass

    def visible_text(self):
        return self.text

    def is_visible(self, selector):
        return False

    def screenshot(self, path):
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n demo")
        self.shot += 1

    def console_messages(self):
        return []

    def http_errors(self):
        return []


def fetcher(url):
    assert url.endswith("/api/diagnostics/bundle")
    return {"browser": {"source_confirmed": "unverified"},
            "evidence_summary": {"unverified": ["browser"]}}


out = Path(".agent-ops/journey-demo-out")
doc = {
    "name": "preflight demo",
    "steps": [
        {"name": "overview loads", "page": "/",
         "assert_text": "UAV Ground Station"},
        {"name": "motors must be armed", "page": "/control",
         "assert_text": ["MOTORS ARMED"],
         "assert_testid": ["motors-armed-indicator"]},
    ],
}
report = journey.run_journey(
    doc, DemoDriver("UAV Ground Station Connecting... DISARMED"),
    out_dir=out, base_url="http://127.0.0.1:8081/", settle=0.0,
    fetcher=fetcher)
code = 0 if report["verdict"] == "pass" else 1
print("EXIT=%d" % code)
print((out / "report.json").read_text())
sys.exit(code)
