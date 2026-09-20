"""Tests for the system capability manifest and its drift prevention."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ground_station.platform.capability_manifest import (
    MANIFEST_PATH,
    check_manifest_drift,
    generate_manifest,
    get_commands,
    get_elf_identity,
    get_firmware_symbols,
    get_panels,
    get_telemetry_keys,
)


def test_capability_manifest_no_drift():
    """Verify that on-disk capability_manifest.json matches current ground truth sources.

    If this test fails, a firmware symbol, command, telemetry key, panel, or route
    was changed without regenerating the manifest. Re-run:
        python -m ground_station.platform.capability_manifest
    """
    is_synced, diff_msg = check_manifest_drift(MANIFEST_PATH)
    assert is_synced, f"Capability manifest drift detected: {diff_msg}. Re-run 'python -m ground_station.platform.capability_manifest'"


def test_capability_manifest_structure():
    """Verify required top-level sections, identity, and staleness caveat."""
    manifest = generate_manifest()

    assert manifest["manifest_version"] == "v1"
    assert "OBJ/" in manifest["staleness_caveat"]
    assert "last build" in manifest["staleness_caveat"]

    # ELF identity
    elf_id = manifest["elf_identity"]
    assert elf_id["elf_path"] == "OBJ/JX_FLY.axf"
    assert elf_id["exists"] is True
    assert len(elf_id["elf_sha256"]) == 64
    assert elf_id["size_bytes"] > 0
    assert "OBJ/" in elf_id["caveat"]

    # Firmware symbols
    syms = manifest["firmware_symbols"]
    assert syms["source"] == "dwarf"
    assert syms["count"] > 300
    assert len(syms["names"]) == syms["count"]
    assert "imu_data" in syms["names"]
    assert "s_ekf" in syms["names"]

    # Commands
    cmds = manifest["commands"]
    assert cmds["count"] >= 20
    assert "0x01" in cmds["commands"]
    assert cmds["commands"]["0x01"]["name"] == "PID_GAIN"
    assert len(cmds["commands"]["0x01"]["parameters"]) == 3
    assert cmds["commands"]["0x04"]["safety_class"]["danger_level"] == "dangerous"

    # Telemetry
    telem = manifest["telemetry"]
    assert "wifi_bridge.py" in telem["source"]
    assert telem["verified_published_keys_total"] > 100
    assert "frame_a_sidebar" in telem["frame_decoders"]
    assert "status.roll_deg" in telem["verified_published_keys"]
    assert "c.gyro_x" in telem["verified_published_keys"]
    assert len(telem["unverified_keys_in_panels"]) > 0

    # Panels
    panels = manifest["panels"]
    assert len(panels) == 17
    panel_names = {p["name"] for p in panels}
    assert "System Overview" in panel_names
    assert "Flight Status" in panel_names
    assert "MRAC Controller" in panel_names
    assert "Time Series" in panel_names
    for p in panels:
        assert p["name"]
        assert p["slug"]
        assert p["file"].endswith(".js")
        assert isinstance(p["workspaces"], list)
        assert len(p["workspaces"]) > 0
        assert isinstance(p["keys_read"], list)

    # Routes
    routes = manifest["routes"]
    assert "/api/manifest" in routes["GET"]
    assert "/api/contract" in routes["GET"]
    assert "/api/symbols" in routes["GET"]
    assert "/commands" in routes["POST"]
