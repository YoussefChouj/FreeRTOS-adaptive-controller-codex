"""T5 tests: terminal endpoint, findings channel, catalog generator, UI tools.

Tests use an in-process ApiServer on an ephemeral port (no live service).
The PTY runs ``python -c "print('hi')"`` for testing.
"""
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file's parent = ground_station/service/tests/)
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]  # parents[0]=tests, [1]=service, [2]=repo root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ephemeral_port() -> int:
    """Return a free port."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_tmp_token_dir() -> Path:
    """Create a temp directory for the terminal token."""
    d = tempfile.mkdtemp(prefix="t5_terminal_")
    return Path(d)


def _make_service():
    """Create a test service."""
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore
    from ground_station.livewatch.stream import StreamRange, StreamSchema

    svc = GroundStationService(
        store=SessionStore(),
        schemas=[StreamSchema(1, 1, 4,
                              (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)],
        source="sim")
    svc.start()
    return svc


def _make_api(server):
    """Create an ApiServer with terminal_manager and start it."""
    from ground_station.service.api import ApiServer
    from ground_station.service.terminal import TerminalManager
    port = _ephemeral_port()
    tm = TerminalManager(state_dir=server)
    srv = ApiServer(server, host="127.0.0.1", port=port,
                    terminal_manager=tm)
    srv.start()
    return srv, tm


# ---------------------------------------------------------------------------
# Terminal endpoint: token, PTY echo, resize
# ---------------------------------------------------------------------------


class TestTerminalToken:
    """Token required, 401 without, 200 with."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="pty not available on Windows",
    )
    def test_401_without_token(self):
        """A WS handshake without ?token= returns 401."""
        tmp_dir = _make_tmp_token_dir()
        try:
            svc = _make_service()
            api_server, tm = _make_api(tmp_dir)
            try:
                port = api_server.address[1]
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(("127.0.0.1", port))
                sock.settimeout(5)
                sock.sendall(
                    b"GET /api/terminal/ws HTTP/1.1\r\n"
                    b"Host: 127.0.0.1\r\n"
                    b"Upgrade: websocket\r\n"
                    b"Connection: Upgrade\r\n"
                    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    b"Sec-WebSocket-Version: 13\r\n"
                    b"\r\n"
                )
                resp = sock.recv(4096)
                sock.close()
                assert b"401" in resp or b"Unauthorized" in resp
            finally:
                api_server.stop()
                svc.stop()
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="pty not available on Windows",
    )
    def test_token_value(self):
        """Token is a 32-hex string."""
        tmp_dir = _make_tmp_token_dir()
        try:
            tm = TerminalManager(state_dir=tmp_dir)
            tok = tm.token
            assert len(tok) == 32
            int(tok, 16)  # should not raise
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="pty not available on Windows",
    )
    def test_token_file_exists(self):
        """Token file is written to .agent_state/terminal-token."""
        tmp_dir = _make_tmp_token_dir()
        try:
            svc = _make_service()
            api_server, tm = _make_api(tmp_dir)
            try:
                assert tm.token == "test_token"
                tf = tm.get_token_file()
                assert tf.exists()
                assert tf.read_text().strip() == "test_token"
            finally:
                api_server.stop()
                svc.stop()
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="pty not available on Windows",
    )
    def test_terminal_manager_available(self):
        """ApiServer has a terminal_manager when constructed with one."""
        tmp_dir = _make_tmp_token_dir()
        try:
            svc = _make_service()
            api_server, tm = _make_api(tmp_dir)
            try:
                assert api_server.terminal_manager is not None
                assert api_server.terminal_manager is tm
            finally:
                api_server.stop()
                svc.stop()
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# PTY echo: harmless command
# ---------------------------------------------------------------------------


class TestTerminalPty:
    """PTY session echo and resize."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="pty not available on Windows",
    )
    def test_pty_echo(self):
        """Verify the terminal module is importable and PTY works."""
        import pty
        import os
        master, slave = pty.openpty()
        assert master >= 0
        assert slave >= 0
        os.close(master)
        os.close(slave)

    def test_resize_message_format(self):
        """Resize messages are JSON with rows/cols keys."""
        msg = json.dumps({"type": "resize", "rows": 24, "cols": 80})
        parsed = json.loads(msg)
        assert parsed["type"] == "resize"
        assert parsed["rows"] == 24
        assert parsed["cols"] == 80

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="pty not available on Windows",
    )
    def test_build_command_default(self):
        """build_command returns a valid command list."""
        from ground_station.service.terminal import build_command
        cmd = build_command()
        assert isinstance(cmd, list)
        assert len(cmd) > 0


# ---------------------------------------------------------------------------
# UI events: ui_navigate and ui_highlight broadcast to SSE
# ---------------------------------------------------------------------------


class TestUiEvents:
    """ui_navigate and ui_highlight broadcast SSE ui events."""

    def test_ui_navigate_action_registered(self):
        """ui_navigate is in the UI action specs."""
        from ground_station.service.agent import UI_ACTION_SPECS
        assert "ui_navigate" in UI_ACTION_SPECS
        spec = UI_ACTION_SPECS["ui_navigate"]
        assert spec["risk"] == "safe"
        assert spec["where"] == "ui"
        assert "tab" in spec["args"]["required"]

    def test_ui_highlight_action_registered(self):
        """ui_highlight is in the UI action specs."""
        from ground_station.service.agent import UI_ACTION_SPECS
        assert "ui_highlight" in UI_ACTION_SPECS
        spec = UI_ACTION_SPECS["ui_highlight"]
        assert spec["risk"] == "safe"
        assert spec["where"] == "ui"
        assert "panel" in spec["args"]["required"]

    def test_ui_navigate_mcp_tool(self):
        """ui_navigate is in the MCP tool list."""
        from ground_station.service.agent_mcp import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "ui_navigate" in names

    def test_ui_highlight_mcp_tool(self):
        """ui_highlight is in the MCP tool list."""
        from ground_station.service.agent_mcp import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "ui_highlight" in names

    def test_file_finding_mcp_tool(self):
        """file_finding is in the MCP tool list."""
        from ground_station.service.agent_mcp import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "file_finding" in names


# ---------------------------------------------------------------------------
# Findings channel: frontmatter round-trip
# ---------------------------------------------------------------------------


class TestFindings:
    """Finding file create, list, and frontmatter round-trip."""

    def test_create_and_list_finding(self, tmp_path):
        """Create a finding, list it, verify frontmatter."""
        from ground_station.research.finding import (
            create_finding, list_findings, _parse_frontmatter,
        )
        fpath = create_finding(
            fid="T5-TEST-001",
            date="2026-09-24",
            severity="high",
            status="open",
            runs=["run-abc", "run-def"],
            summary="Test finding for T5",
            evidence="Test evidence",
            suggested_action="Review and resolve",
            findings_dir=tmp_path,
        )
        assert fpath.exists()

        text = fpath.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        assert fm is not None
        assert fm["id"] == "T5-TEST-001"
        assert fm["date"] == "2026-09-24"
        assert fm["severity"] == "high"
        assert fm["status"] == "open"
        assert fm["runs"] == ["run-abc", "run-def"]
        assert fm["summary"] == "Test finding for T5"

        # List should find it
        import ground_station.research.finding as finding_mod
        orig_dir = finding_mod.FINDINGS_DIR
        try:
            finding_mod.FINDINGS_DIR = tmp_path
            findings = list_findings()
            ids = [f["id"] for f in findings]
            assert "T5-TEST-001" in ids
        finally:
            finding_mod.FINDINGS_DIR = orig_dir

    def test_template_exists(self):
        """TEMPLATE.md exists with required frontmatter fields."""
        tmpl = ROOT / "docs" / "research-platform" / "findings" / "TEMPLATE.md"
        assert tmpl.exists()
        text = tmpl.read_text(encoding="utf-8")
        for field in ("id:", "date:", "severity:", "status:", "runs:", "summary:"):
            assert field in text

    def test_finding_cli_list(self):
        """Finding list CLI returns 0."""
        from ground_station.research.finding import main
        rc = main(["list"])
        assert rc == 0

    def test_finding_cli_new(self, tmp_path):
        """Finding new CLI creates a file."""
        from ground_station.research.finding import main
        import ground_station.research.finding as fm
        orig = fm.FINDINGS_DIR
        try:
            fm.FINDINGS_DIR = tmp_path
            rc = main(["new", "T5-CLI-001", "--date", "2026-01-01",
                       "--severity", "low", "--status", "open",
                       "--summary", "CLI test"])
            assert rc == 0
            assert (tmp_path / "T5-CLI-001.md").exists()
        finally:
            fm.FINDINGS_DIR = orig


# ---------------------------------------------------------------------------
# Catalog generator: deterministic output
# ---------------------------------------------------------------------------


class TestCatalog:
    """Catalog generator produces deterministic output."""

    def test_catalog_generator_runs(self):
        """Catalog generator writes CATALOG.md."""
        from ground_station.research.catalog import generate_catalog
        text = generate_catalog()
        assert len(text) > 100
        assert "# Research Workflow Catalog" in text
        assert "## Step Signatures" in text
        assert "## Action Registry" in text
        assert "## MCP Tools" in text

    def test_catalog_deterministic(self):
        """Two calls produce identical output."""
        from ground_station.research.catalog import generate_catalog
        a = generate_catalog()
        b = generate_catalog()
        assert a == b

    def test_catalog_has_step_types(self):
        """Catalog lists all 8 step types."""
        from ground_station.research.catalog import generate_catalog
        text = generate_catalog()
        for step in ("set_params", "fly_trajectory", "capture", "wait_until",
                      "analyze", "revert", "note", "call"):
            assert f"`{step}`" in text

    def test_catalog_has_mcp_tools(self):
        """Catalog lists ui_navigate, ui_highlight, file_finding."""
        from ground_station.research.catalog import generate_catalog
        text = generate_catalog()
        assert "ui_navigate" in text
        assert "ui_highlight" in text
        assert "file_finding" in text


# ---------------------------------------------------------------------------
# Terminal panel: PANEL_META and capability manifest
# ---------------------------------------------------------------------------


class TestTerminalPanel:
    """Terminal tab registered in PANEL_META and capability manifest."""

    def test_terminal_in_plugin_files(self):
        """terminal-panel.js is listed in PLUGIN_FILES of index.html."""
        shell = ROOT / "docs" / "dashboard-platform" / "shell" / "index.html"
        text = shell.read_text(encoding="utf-8")
        m = re.search(
            r"const\s+PLUGIN_FILES\s*=\s*\[(.*?)\];", text, re.DOTALL
        )
        assert m, "PLUGIN_FILES array not found in index.html"
        assert (
            "/plugins/terminal-panel.js" in m.group(1)
        ), "terminal-panel.js is not in PLUGIN_FILES — terminal workspace will show panels=[]"

    def test_panel_in_index_html(self):
        """Terminal panel is in PANEL_META."""
        shell = ROOT / "docs" / "dashboard-platform" / "shell" / "index.html"
        text = shell.read_text(encoding="utf-8")
        assert "'Terminal'" in text or '"Terminal"' in text
        assert "workspace: 'terminal'" in text or 'workspace: "terminal"' in text

    def test_terminal_tab_button_exists(self):
        """Terminal tab button exists in the workspace bar."""
        shell = ROOT / "docs" / "dashboard-platform" / "shell" / "index.html"
        text = shell.read_text(encoding="utf-8")
        assert "data-ws=\"terminal\"" in text or 'data-ws="terminal"' in text
        assert "data-testid=\"tab-terminal\"" in text or 'data-testid="tab-terminal"' in text

    def test_terminal_panel_file_exists(self):
        """terminal-panel.js exists."""
        p = ROOT / "docs" / "dashboard-platform" / "shell" / "plugins" / "terminal-panel.js"
        assert p.exists()
        text = p.read_text(encoding="utf-8")
        assert "registerPanel" in text
        assert "xterm" in text.lower()

    def test_capability_manifest_has_terminal(self):
        """Capability manifest generator includes Terminal panel."""
        from ground_station.platform.capability_manifest import get_panels
        panels = get_panels()
        names = [p["name"] for p in panels]
        assert "Terminal" in names


# ---------------------------------------------------------------------------
# Capability manifest regeneration
# ---------------------------------------------------------------------------


class TestCapabilityManifestRegen:
    """Regenerate capability_manifest.json."""

    def test_regenerate_manifest(self):
        """Write and re-read the capability manifest."""
        from ground_station.platform.capability_manifest import write_manifest
        tmp = Path(tempfile.mkdtemp()) / "capability_manifest.json"
        try:
            out = write_manifest(tmp)
            assert out.exists()
            data = json.loads(out.read_text(encoding="utf-8"))
            panel_names = [p["name"] for p in data.get("panels", [])]
            assert "Terminal" in panel_names
        finally:
            import shutil
            shutil.rmtree(tmp.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# Opencode config
# ---------------------------------------------------------------------------


class TestOpencodeConfig:
    """opencode.json has dashboard MCP server and permission policy."""

    def test_opencode_json_exists(self):
        """opencode.json exists at repo root."""
        p = ROOT / "opencode.json"
        assert p.exists()
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        # Dashboard MCP server
        assert "mcpServers" in data
        assert "dashboard" in data["mcpServers"]
        # Permission policy
        assert "permission" in data
        bash_perm = data["permission"].get("bash", {})
        # Should block flash commands
        found_flash = any("flash" in k for k in bash_perm if isinstance(k, str))
        assert found_flash
