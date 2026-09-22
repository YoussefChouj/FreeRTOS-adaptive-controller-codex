"""Paths shared across the agent-map package."""
from __future__ import annotations

from pathlib import Path

# ground_station/agent_map/__init__.py -> parents[2] = project root
ROOT = Path(__file__).resolve().parents[2]

# Firmware source dirs we scan (excludes FreeRTOS/, stm32_lib/, OBJ/).
FIRMWARE_DIRS = ("API", "TASK", "BSP", "USER", "Global_file")

ELF_PATH = ROOT / "OBJ" / "JX_FLY.axf"

AGENT_MAP_DIR = ROOT / "docs" / "agent-map"
OUT_PATH = AGENT_MAP_DIR / "agent_map.json"
MODULES_YAML = AGENT_MAP_DIR / "modules.yaml"

LIVEWATCH_DIR = ROOT / "ground_station" / "livewatch"
MANIFESTS_YAML = LIVEWATCH_DIR / "manifests.yaml"
PRESETS_YAML = LIVEWATCH_DIR / "multi_slot_presets.yaml"

GLOSSARY_MD = ROOT / "docs" / "glossary.md"


def glob_firmware() -> list[Path]:
    files: list[Path] = []
    for d in FIRMWARE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() in (".c", ".h") and p.is_file():
                files.append(p)
    return files