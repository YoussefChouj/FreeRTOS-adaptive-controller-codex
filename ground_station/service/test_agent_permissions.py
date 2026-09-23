"""Tests for ground_station.service.agent_permissions.

Unit tests for build_permission_manifest, build_llms_txt, and their
constants (SCHEMA_VERSION, SELECTOR_RULES, ACTION_GUIDELINES).

Route-level tests fire an in-process ApiServer on a random ephemeral
port and hit it via http.client -- never the live 8081 service.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

# Bypass any local proxy so http.client talks directly to 127.0.0.1.
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

# ---- module under test ----------------------------------------------------

from ground_station.service.agent_permissions import (
    ACTION_GUIDELINES,
    SCHEMA_VERSION,
    SELECTOR_RULES,
    build_llms_txt,
    build_permission_manifest,
)
from ground_station.service.api import ApiServer

# ---- helpers --------------------------------------------------------------

def _get(path: str, host: str, port: int) -> tuple[int, str, dict | None]:
    """Minimal GET that returns (status, raw_body_str, parsed_json|None)."""
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    ct = resp.getheader("Content-Type", "")
    conn.close()
    parsed = None
    if "application/json" in ct:
        try:
            parsed = json.loads(raw)
        except Exception:
            pass
    return resp.status, raw, parsed


def _fake_service():
    """A minimal service shim for ApiServer.__init__."""
    class FakeService:
        gateway = None  # gateway_polling thread expects this attribute

        def add_listener(self, fn):
            pass

        def remove_listener(self, fn):
            pass
    return FakeService()


# ===================================================================
# Unit tests -- pure functions, no server
# ===================================================================

class TestBuildPermissionManifest:
    """build_permission_manifest returns the correct LAS-WG shape."""

    def test_top_level_keys(self):
        manifest = build_permission_manifest([], {})
        expected = {"metadata", "safety_context", "resource_rules",
                    "action_guidelines", "api"}
        assert set(manifest.keys()) == expected

    def test_schema_version_matches_constant(self):
        manifest = build_permission_manifest([], {})
        assert manifest["metadata"]["schema_version"] == SCHEMA_VERSION

    def test_generated_at_is_iso8601_utc(self):
        manifest = build_permission_manifest([], {})
        ts = manifest["metadata"]["generated_at"]
        assert ts.endswith("Z"), f"timestamp must end in Z, got {ts!r}"
        # Must be parseable as ISO-8601.
        _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_author_and_subject(self):
        manifest = build_permission_manifest([], {})
        assert manifest["metadata"]["author"] == "ground_station.service"
        assert "UAV" in manifest["metadata"]["subject"]

    def test_safety_context_controls_hardware(self):
        manifest = build_permission_manifest([], {})
        assert manifest["safety_context"]["controls_physical_hardware"] is True

    def test_control_mode_passed_through(self):
        for mode in ("supervised", "autonomous", "off"):
            ctl = {"mode": mode, "allow_agent_arm": False, "tier0_access": False}
            m = build_permission_manifest([], ctl)
            assert m["safety_context"]["control_mode"] == mode

    def test_allow_agent_arm_passed_through(self):
        ctl_true = {"mode": "test", "allow_agent_arm": True, "tier0_access": False}
        ctl_false = {"mode": "test", "allow_agent_arm": False, "tier0_access": True}
        m1 = build_permission_manifest([], ctl_true)
        m2 = build_permission_manifest([], ctl_false)
        assert m1["safety_context"]["allow_agent_arm"] is True
        assert m2["safety_context"]["allow_agent_arm"] is False
        assert m2["safety_context"]["tier0_access"] is True
        assert m1["safety_context"]["tier0_access"] is False

    def test_control_passed_through_with_multiple_dicts(self):
        """At least two different control dicts must show pass-through."""
        ctl_a = {"mode": "manual", "allow_agent_arm": True, "tier0_access": True}
        ctl_b = {"mode": "supervised", "allow_agent_arm": False, "tier0_access": None}
        m_a = build_permission_manifest([], ctl_a)
        m_b = build_permission_manifest([], ctl_b)

        assert m_a["safety_context"]["control_mode"] == "manual"
        assert m_a["safety_context"]["allow_agent_arm"] is True
        assert m_a["safety_context"]["tier0_access"] is True

        assert m_b["safety_context"]["control_mode"] == "supervised"
        assert m_b["safety_context"]["allow_agent_arm"] is False
        assert m_b["safety_context"]["tier0_access"] is None

    def test_none_inputs_no_crash(self):
        manifest = build_permission_manifest(None, None)
        assert isinstance(manifest, dict)
        assert manifest["safety_context"]["controls_physical_hardware"] is True

    def test_resource_rules_is_selector_rules(self):
        manifest = build_permission_manifest([], {})
        assert manifest["resource_rules"] is SELECTOR_RULES

    def test_action_guidelines_is_module_constant(self):
        manifest = build_permission_manifest([], {})
        assert manifest["action_guidelines"] is ACTION_GUIDELINES

    def test_risk_groups_action_names(self):
        actions = [
            {"name": "arm", "risk": "critical", "description": "arm motor"},
            {"name": "read_param", "risk": "low", "description": "read"},
            {"name": "read_param2", "risk": "low", "description": "read"},
            {"name": "set_param", "risk": "critical", "description": "write"},
        ]
        manifest = build_permission_manifest(actions, {})
        by_risk = manifest["api"]["action_names_by_risk"]
        assert set(by_risk.keys()) == {"critical", "low"}
        assert by_risk["critical"] == ["arm", "set_param"]
        assert by_risk["low"] == ["read_param", "read_param2"]

    def test_action_names_sorted_within_risk(self):
        actions = [
            {"name": "zebra", "risk": "medium"},
            {"name": "alpha", "risk": "medium"},
            {"name": "middle", "risk": "low"},
        ]
        manifest = build_permission_manifest(actions, {})
        for risk_list in manifest["api"]["action_names_by_risk"].values():
            assert risk_list == sorted(risk_list)

    def test_risk_unknown_for_missing_risk_field(self):
        actions = [{"name": "ghost"}]  # no risk key
        manifest = build_permission_manifest(actions, {})
        assert "ghost" in manifest["api"]["action_names_by_risk"].get("unknown", [])


class TestSelectorRulesInvariant:
    """Every entry in SELECTOR_RULES has the required fields and the
    allowed=False => requires_human_approval invariant."""

    def test_every_rule_has_required_fields(self):
        for i, rule in enumerate(SELECTOR_RULES):
            assert "verb" in rule, f"rule {i} missing verb"
            assert "selector" in rule, f"rule {i} missing selector"
            assert "allowed" in rule, f"rule {i} missing allowed"
            assert "description" in rule, f"rule {i} missing description"

    def test_allowed_is_real_bool(self):
        for i, rule in enumerate(SELECTOR_RULES):
            assert isinstance(rule["allowed"], bool), (
                f"rule {i} allowed={rule['allowed']!r} is not bool"
            )

    def test_not_allowed_requires_human_approval(self):
        for i, rule in enumerate(SELECTOR_RULES):
            if rule["allowed"] is False:
                modifiers = rule.get("modifiers", {})
                assert modifiers.get("requires_human_approval") is True, (
                    f"rule {i} has allowed=False but "
                    f"modifiers.requires_human_approval={modifiers.get('requires_human_approval')!r}"
                )

    def test_allowed_rules_can_have_optional_modifiers(self):
        """Allowed rules may still carry modifiers (e.g. staging note)."""
        for i, rule in enumerate(SELECTOR_RULES):
            if rule["allowed"] is True:
                assert "modifiers" not in rule or isinstance(
                    rule.get("modifiers"), dict
                ), f"rule {i} has unexpected modifiers type"


class TestBuildLlmsTxt:
    """build_llms_txt returns valid Markdown with the mode interpolated."""

    def test_non_empty(self):
        txt = build_llms_txt({})
        assert txt, "llms.txt must not be empty"

    def test_starts_with_heading(self):
        txt = build_llms_txt({})
        assert txt.lstrip().startswith("# "), (
            f"llms.txt should start with '# ', got: {txt[:50]!r}"
        )

    def test_contains_mode(self):
        for mode in ("supervised", "autonomous", "manual"):
            ctl = {"mode": mode}
            txt = build_llms_txt(ctl)
            assert mode in txt, f"mode {mode!r} not found in llms.txt"

    def test_default_mode_unknown(self):
        txt = build_llms_txt({})
        assert "unknown" in txt

    def test_no_control_at_all(self):
        txt = build_llms_txt(None)
        assert "unknown" in txt

    def test_contains_expected_links(self):
        txt = build_llms_txt({})
        assert "/.well-known/agent-permissions.json" in txt
        assert "/api/routes" in txt
        assert "/api/agent/actions" in txt


# ===================================================================
# Route-level tests -- in-process ApiServer
# ===================================================================

def _start_test_server():
    """Start an ApiServer on an ephemeral port and return (server, port)."""
    svc = _fake_service()
    srv = ApiServer(
        svc,
        host="127.0.0.1",
        port=0,
        static_root=str(
            Path(__file__).parents[3] / "docs" / "dashboard-platform" / "shell"
        ),
    )
    # The server thread must be alive before we make requests.
    srv.start()
    # Give the server a moment to bind and start accepting connections.
    for _ in range(50):
        if srv.thread.is_alive():
            break
        time.sleep(0.05)
    port = srv.server.server_address[1]
    return srv, port


class TestAgentPermissionsRoutes:
    """In-process route tests for the two new endpoints and /api/routes."""

    @pytest.fixture(autouse=True)
    def _one_server(self):
        """Start a server once per class, tear down after."""
        self.srv, self.port = _start_test_server()
        try:
            yield
        finally:
            self.srv.stop()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_well_known_returns_200(self):
        status, raw, body = _get("/.well-known/agent-permissions.json",
                                  "127.0.0.1", self.port)
        assert status == 200, f"expected 200, got {status}: {raw[:200]}"

    def test_well_known_content_type_json(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/.well-known/agent-permissions.json")
        resp = conn.getresponse()
        ct = resp.getheader("Content-Type", "")
        conn.close()
        assert "application/json" in ct

    def test_well_known_parses_json(self):
        _, _, body = _get("/.well-known/agent-permissions.json",
                          "127.0.0.1", self.port)
        assert body is not None
        assert isinstance(body, dict)
        assert set(body.keys()) == {
            "metadata", "safety_context", "resource_rules",
            "action_guidelines", "api",
        }

    def test_well_known_has_correct_metadata(self):
        _, _, body = _get("/.well-known/agent-permissions.json",
                          "127.0.0.1", self.port)
        meta = body["metadata"]
        assert meta["schema_version"] == SCHEMA_VERSION
        assert meta["generated_at"].endswith("Z")

    def test_well_known_safety_hardware_true(self):
        _, _, body = _get("/.well-known/agent-permissions.json",
                          "127.0.0.1", self.port)
        assert body["safety_context"]["controls_physical_hardware"] is True

    def test_llms_txt_returns_200(self):
        status, raw, _ = _get("/llms.txt", "127.0.0.1", self.port)
        assert status == 200, f"expected 200, got {status}: {raw[:200]}"

    def test_llms_txt_content_type_markdown(self):
        status, raw, _ = _get("/llms.txt", "127.0.0.1", self.port)
        assert status == 200
        # We don't get the header from _get, re-fetch for header check.
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/llms.txt")
        resp = conn.getresponse()
        ct = resp.getheader("Content-Type", "")
        conn.close()
        assert "text/markdown" in ct, f"Content-Type={ct!r}"
        assert "charset=utf-8" in ct, f"Content-Type={ct!r}"

    def test_llms_txt_non_empty_markdown(self):
        status, raw, _ = _get("/llms.txt", "127.0.0.1", self.port)
        assert status == 200
        assert raw.lstrip().startswith("# ")

    def test_api_routes_contains_new_keys(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/routes")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        resp.close()
        conn.close()
        # /api/routes returns _ROUTE_MAP = {"GET": {...}, "POST": {...}}
        route_map = body.get("GET", body) if isinstance(body, dict) else {}
        assert "/.well-known/agent-permissions.json" in route_map
        assert "/llms.txt" in route_map

    def test_api_routes_value_descriptions_present(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/routes")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        resp.close()
        conn.close()
        route_map = body.get("GET", body) if isinstance(body, dict) else {}
        perm_desc = route_map["/.well-known/agent-permissions.json"]
        assert isinstance(perm_desc, str) and len(perm_desc) > 0
        llms_desc = route_map["/llms.txt"]
        assert isinstance(llms_desc, str) and len(llms_desc) > 0
