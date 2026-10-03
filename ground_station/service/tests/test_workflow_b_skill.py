"""Workflow B part f: the /workflow-b launch skill names things that exist and keeps the fence numbers."""

from __future__ import annotations

import re
from pathlib import Path

from ground_station.service.campaign_schema import load_campaign

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".claude" / "skills" / "workflow-b" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter():
    head = _text().split("---")[1]
    assert re.search(r"^name: workflow-b$", head, re.M)
    assert re.search(r"^description: .*operator arms by RC", head, re.M)


def test_fence_numbers_match_decision_2():
    text = _text()
    assert "hard z 1.7 m, |x| 1.6 m, |y| 2.0 m" in text
    assert "Soft boundary 0.3 m inside (z 1.4, |x| 1.3, |y| 1.7)" in text


def test_named_mcp_tools_exist():
    mcp = (ROOT / "ground_station" / "service" / "agent_mcp.py").read_text(encoding="utf-8")
    for tool in ("campaign_state", "campaign_go", "campaign_pause", "campaign_land", "campaign_abort"):
        assert f"`{tool}`" in _text()
        assert f'"{tool}"' in mcp or f"def {tool}(" in mcp, tool


def test_checklist_keys_match_the_campaign_panel():
    harness = (ROOT / "ground_station" / "service" / "tests" / "campaign_panel_harness.js").read_text(encoding="utf-8")
    keys = re.findall(r"(\w+): true", harness.split("expectedChecklist = {", 1)[1].split("}", 1)[0])
    assert keys
    listed = _text().split("all true (`", 1)[1].split("`)", 1)[0]
    assert [k.strip() for k in listed.replace("\n", " ").split(",")] == keys


def test_named_paths_exist_and_the_ladder_validates():
    text = _text()
    for rel in ("ground_station/service/campaigns", "docs/workflow-b/scenarios", "docs/skills/flight-campaign.md"):
        assert rel in text and (ROOT / rel).exists(), rel
    ladder = load_campaign(ROOT / "ground_station" / "service" / "campaigns" / "hover_ladder.yaml")
    assert ladder.mode == "fly" and [e.name for e in ladder.experiments] == ["hover_z050", "hover_z070", "hover_z130"]


def test_go_is_the_arm_consent():
    """Operator decision 2026-10-03: Go consents to arming; no allow_agent_arm step in the panel or the skill."""
    panel = (ROOT / "docs" / "dashboard-platform" / "shell" / "plugins" / "campaign-panel.js").read_text(encoding="utf-8")
    assert "Allow agent arm" not in panel and "cp-consent-note" in panel
    text = _text()
    assert "allow_agent_arm" not in text and "Go is the operator's consent" in text


def test_no_direct_http_go():
    for line in _text().splitlines():
        if "/api/campaign" in line:
            assert "never" in line.lower(), line
    assert "You never POST to the 8081 HTTP API yourself" in _text()


def test_flight_campaign_points_here():
    doc = (ROOT / "docs" / "skills" / "flight-campaign.md").read_text(encoding="utf-8")
    assert "`/workflow-b`" in doc and ".claude/skills/workflow-b/SKILL.md" in doc
