"""Tests for the flight-campaign skill document."""

from __future__ import annotations

from pathlib import Path
import re

import yaml

from ground_station.service.campaign_schema import parse_campaign

def get_skill_path() -> Path:
    # repo root = parents[3]
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    skill_path = repo_root / "docs" / "skills" / "flight-campaign.md"
    return skill_path

def test_1_frontmatter():
    """Test that the skill frontmatter parses correctly."""
    content = get_skill_path().read_text(encoding="utf-8")
    
    # Match the block between the first two --- lines
    match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL | re.MULTILINE)
    assert match is not None, "Could not find frontmatter block"
    
    frontmatter_text = match.group(1)
    data = yaml.safe_load(frontmatter_text)
    
    assert data["name"] == "flight-campaign"
    assert isinstance(data["description"], str)
    assert len(data["description"].strip()) > 0

def test_2_example_campaign_validates():
    """Test that the first example campaign YAML block passes parse_campaign."""
    content = get_skill_path().read_text(encoding="utf-8")
    
    # Extract the first ```yaml block
    match = re.search(r"```yaml\n(.*?)\n```", content, re.DOTALL)
    assert match is not None, "Could not find first ```yaml block"
    
    yaml_text = match.group(1)
    campaign_data = yaml.safe_load(yaml_text)
    
    # parse_campaign should return a Campaign and not raise CampaignError
    campaign = parse_campaign(campaign_data)
    assert campaign.campaign is not None

def test_3_text_requirements():
    """Test for required phrases and safety rules regarding /api/campaign/go."""
    content = get_skill_path().read_text(encoding="utf-8")
    
    assert "load_campaign" in content
    assert "Campaign panel" in content
    
    lines = content.splitlines()
    for line in lines:
        if "/api/campaign/go" in line:
            assert "never" in line or "Never" in line, f"Safety rule violated in line: {line}"

