"""WP-35 design system: runs ``ui_components_harness.js`` (kit, flight strip, shell wiring, no-npm lint rules)
under Node and checks the service serves the shared ``/ui/`` files the shell links before any plugin."""
from __future__ import annotations

import http.client
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HARNESS = Path(__file__).with_name("ui_components_harness.js")
TAGS = ["K%d" % i for i in range(1, 14)] + ["S%d" % i for i in range(1, 8)] + ["H1", "H2", "L1", "L2", "L3"]


def test_components_harness_all_checks_pass():
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("node not available on PATH")
    proc = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, encoding="utf-8",
                          timeout=120, cwd=str(ROOT))
    assert proc.returncode == 0, "ui components harness failed:\n" + proc.stdout + proc.stderr
    assert "ALL CHECKS PASSED" in proc.stdout
    lines = proc.stdout.splitlines()
    missing = [t for t in TAGS if not any(line.startswith(t + " ") for line in lines)]
    assert not missing, "missing check tags: %s" % missing


@pytest.fixture
def shell_api():
    from ground_station.service.api import ApiServer
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore

    svc = GroundStationService(store=SessionStore(), source="sim")
    svc.start()
    server = ApiServer(svc, static_root=ROOT / "docs" / "dashboard-platform" / "shell")
    server.start()
    try:
        yield "127.0.0.1", server.address[1]
    finally:
        server.stop()
        svc.stop()


@pytest.mark.parametrize("path, mime", [
    ("/ui/tokens.css", "text/css"),
    ("/ui/components.css", "text/css"),
    ("/ui/ui-kit.js", "application/javascript"),
    ("/plugins/flight-strip.js", "application/javascript"),
])
def test_shared_ui_files_are_served(shell_api, path, mime):
    conn = http.client.HTTPConnection(*shell_api, timeout=30)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    assert resp.status == 200 and resp.getheader("Content-Type", "").startswith(mime) and body
    assert "no-cache" in resp.getheader("Cache-Control", "")
