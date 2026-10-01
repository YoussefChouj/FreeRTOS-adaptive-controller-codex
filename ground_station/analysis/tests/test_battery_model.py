"""Tests for battery model module and pack registry."""
from __future__ import annotations

import math
from pathlib import Path
import pytest
import yaml

from ground_station.analysis.battery_model import PackError, PackRegistry


def test_shipped_packs_loads() -> None:
    """1. The shipped packs.yaml loads; pack_ids() == ["P5300-1", "P4000-1", "P4000-2"]."""
    registry = PackRegistry.load()
    assert registry.pack_ids() == ["P5300-1", "P4000-1", "P4000-2"]


def test_predict_soc_values() -> None:
    """2. predict_soc: 16.8 V -> 100; 13.08 V -> 0; 15.36 V (3.84 V/cell) -> 50;

    a voltage halfway between two table rows gives the halfway SoC (pytest.approx, abs=1e-6);
    above 16.8 V clamps to 100; below clamps to 0.
    """
    registry = PackRegistry.load()

    # 16.8 V -> 100
    assert registry.predict_soc("P5300-1", 16.8) == pytest.approx(100.0, abs=1e-6)

    # 13.08 V -> 0
    assert registry.predict_soc("P5300-1", 13.08) == pytest.approx(0.0, abs=1e-6)

    # 15.36 V (3.84 V/cell) -> 50
    assert registry.predict_soc("P5300-1", 15.36) == pytest.approx(50.0, abs=1e-6)

    # Halfway between [3.87, 60] and [3.85, 55]:
    # v_cell = (3.87 + 3.85) / 2 = 3.86 V/cell -> 3.86 * 4 = 15.44 V
    # SoC should be halfway: (60 + 55) / 2 = 57.5%
    assert registry.predict_soc("P5300-1", 15.44) == pytest.approx(57.5, abs=1e-6)

    # Above 16.8 V clamps to 100
    assert registry.predict_soc("P5300-1", 17.5) == pytest.approx(100.0, abs=1e-6)

    # Below 13.08 V clamps to 0
    assert registry.predict_soc("P5300-1", 11.5) == pytest.approx(0.0, abs=1e-6)


def test_predict_soc_invalid_inputs() -> None:
    """3. predict_soc raises PackError for an unknown pack, NaN and infinity."""
    registry = PackRegistry.load()

    with pytest.raises(PackError):
        registry.predict_soc("NON_EXISTENT_PACK", 16.0)

    with pytest.raises(PackError):
        registry.predict_soc("P5300-1", float("nan"))

    with pytest.raises(PackError):
        registry.predict_soc("P5300-1", float("inf"))

    with pytest.raises(PackError):
        registry.predict_soc("P5300-1", float("-inf"))


def test_per_pack_ocv_table_override(tmp_path: Path) -> None:
    """4. A per-pack ocv_table replaces the shared one for that pack only."""
    custom_yaml = """
gate_soc_pct: 30.0
min_rest_s: 60.0
sag_v_per_cell: 3.55
default_flight_drop_pct: 10.0
ocv_table:
  - [4.20, 100]
  - [3.80, 40]
  - [3.27, 0]
packs:
  - id: "PACK-SHARED"
    label: "Uses shared table"
    cells: 4
    capacity_mah: 4000
  - id: "PACK-CUSTOM"
    label: "Uses custom table"
    cells: 4
    capacity_mah: 4000
    ocv_table:
      - [4.20, 100]
      - [3.80, 80]
      - [3.27, 0]
"""
    p = tmp_path / "custom_packs.yaml"
    p.write_text(custom_yaml, encoding="utf-8")
    registry = PackRegistry.load(p)

    # At 15.2 V on 4S (3.80 V/cell):
    # PACK-SHARED should interpolate to 40.0%
    # PACK-CUSTOM should interpolate to 80.0%
    assert registry.predict_soc("PACK-SHARED", 15.2) == pytest.approx(40.0, abs=1e-6)
    assert registry.predict_soc("PACK-CUSTOM", 15.2) == pytest.approx(80.0, abs=1e-6)


def test_load_three_problems_together(tmp_path: Path) -> None:
    """5a. One temporary file with three different problems raises one PackError naming all three."""
    bad_yaml = """
gate_soc_pct: 125.0
min_rest_s: "invalid_str"
sag_v_per_cell: 3.55
default_flight_drop_pct: 10.0
ocv_table:
  - [4.20, 100]
  - [3.27, 0]
packs: []
"""
    p = tmp_path / "three_problems.yaml"
    p.write_text(bad_yaml, encoding="utf-8")

    with pytest.raises(PackError) as exc_info:
        PackRegistry.load(p)

    msg = str(exc_info.value)
    lines = [line.strip() for line in msg.splitlines() if line.strip()]
    assert len(lines) == 3
    assert any("gate_soc_pct outside 0..100" in line for line in lines)
    assert any("min_rest_s" in line for line in lines)
    assert any("empty pack list" in line for line in lines)


@pytest.mark.parametrize(
    "mutator,expected_error_substr",
    [
        # Missing top-level keys
        (lambda d: d.pop("gate_soc_pct"), "missing or non-numeric top-level key: gate_soc_pct"),
        (lambda d: d.pop("min_rest_s"), "missing or non-numeric top-level key: min_rest_s"),
        (lambda d: d.pop("sag_v_per_cell"), "missing or non-numeric top-level key: sag_v_per_cell"),
        (lambda d: d.pop("default_flight_drop_pct"), "missing or non-numeric top-level key: default_flight_drop_pct"),
        (lambda d: d.pop("ocv_table"), "missing or non-numeric top-level key: ocv_table"),
        (lambda d: d.pop("packs"), "missing or non-numeric top-level key: packs"),
        # Non-numeric top-level keys
        (lambda d: d.update({"gate_soc_pct": "not_a_number"}), "missing or non-numeric top-level key: gate_soc_pct"),
        (lambda d: d.update({"min_rest_s": True}), "missing or non-numeric top-level key: min_rest_s"),
        (lambda d: d.update({"sag_v_per_cell": None}), "missing or non-numeric top-level key: sag_v_per_cell"),
        (lambda d: d.update({"default_flight_drop_pct": float("nan")}), "missing or non-numeric top-level key: default_flight_drop_pct"),
        # gate_soc_pct outside 0..100
        (lambda d: d.update({"gate_soc_pct": 105.0}), "gate_soc_pct outside 0..100"),
        (lambda d: d.update({"gate_soc_pct": -5.0}), "gate_soc_pct outside 0..100"),
        # Table with fewer than 2 rows
        (lambda d: d.update({"ocv_table": [[4.20, 100]]}), "a table with fewer than 2 rows"),
        (lambda d: d.update({"ocv_table": []}), "a table with fewer than 2 rows"),
        # Table voltages not strictly descending
        (lambda d: d.update({"ocv_table": [[3.80, 100], [4.20, 0]]}), "table voltages not strictly descending"),
        (lambda d: d.update({"ocv_table": [[4.00, 100], [4.00, 0]]}), "table voltages not strictly descending"),
        # Table soc values not non-increasing or outside 0..100
        (lambda d: d.update({"ocv_table": [[4.20, 50], [3.27, 80]]}), "table soc values not non-increasing or outside 0..100"),
        (lambda d: d.update({"ocv_table": [[4.20, 110], [3.27, 0]]}), "table soc values not non-increasing or outside 0..100"),
        (lambda d: d.update({"ocv_table": [[4.20, 100], [3.27, -5]]}), "table soc values not non-increasing or outside 0..100"),
        # Duplicate pack id
        (lambda d: d["packs"].append({"id": "P5300-1", "label": "dup", "cells": 4, "capacity_mah": 5000}), "duplicate pack id"),
        # Pack with cells < 1 or capacity_mah <= 0
        (lambda d: d["packs"][0].update({"cells": 0}), "pack with cells < 1 or capacity_mah <= 0"),
        (lambda d: d["packs"][0].update({"capacity_mah": 0}), "pack with cells < 1 or capacity_mah <= 0"),
        (lambda d: d["packs"][0].update({"capacity_mah": -500}), "pack with cells < 1 or capacity_mah <= 0"),
        # Empty pack list
        (lambda d: d.update({"packs": []}), "empty pack list"),
    ],
)
def test_load_single_problems(tmp_path: Path, mutator, expected_error_substr: str) -> None:
    """5b. Each single problem listed also raises PackError."""
    base_data = {
        "gate_soc_pct": 30.0,
        "min_rest_s": 60.0,
        "sag_v_per_cell": 3.55,
        "default_flight_drop_pct": 10.0,
        "ocv_table": [
            [4.20, 100],
            [3.27, 0],
        ],
        "packs": [
            {"id": "P5300-1", "label": "5300 mAh #1", "cells": 4, "capacity_mah": 5300},
        ],
    }
    mutator(base_data)
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump(base_data), encoding="utf-8")

    with pytest.raises(PackError) as exc_info:
        PackRegistry.load(p)

    assert expected_error_substr in str(exc_info.value)


def test_load_unreadable_file_and_invalid_yaml(tmp_path: Path) -> None:
    """5c. Unreadable file or invalid YAML also raises PackError."""
    # Non-existent file
    with pytest.raises(PackError) as exc_info:
        PackRegistry.load(tmp_path / "does_not_exist.yaml")
    assert "unreadable file or invalid YAML" in str(exc_info.value)

    # Invalid YAML syntax
    broken_yaml_file = tmp_path / "syntax_error.yaml"
    broken_yaml_file.write_text("gate_soc_pct: [invalid yaml\n  - missing close", encoding="utf-8")
    with pytest.raises(PackError) as exc_info:
        PackRegistry.load(broken_yaml_file)
    assert "unreadable file or invalid YAML" in str(exc_info.value)

    # YAML root is not a dict
    not_a_mapping = tmp_path / "scalar.yaml"
    not_a_mapping.write_text("Just a string", encoding="utf-8")
    with pytest.raises(PackError) as exc_info:
        PackRegistry.load(not_a_mapping)
    assert "unreadable file or invalid YAML" in str(exc_info.value)


def test_expected_drop_and_record_flight() -> None:
    """6. expected_drop: default before any record; mean after two records;

    record_flight rejects a zero, a negative and a NaN drop and leaves the mean unchanged.
    """
    registry = PackRegistry.load()

    # Default before any record
    assert registry.expected_drop("P5300-1") == pytest.approx(10.0, abs=1e-6)

    # Record two flights: drops of 12.0 and 8.0 -> mean 10.0
    registry.record_flight("P5300-1", 90.0, 78.0)  # drop = 12.0
    registry.record_flight("P5300-1", 78.0, 70.0)  # drop = 8.0
    assert registry.expected_drop("P5300-1") == pytest.approx(10.0, abs=1e-6)

    # Record a third flight with drop 14.0 -> mean 34/3
    registry.record_flight("P5300-1", 70.0, 56.0)  # drop = 14.0
    expected_mean = (12.0 + 8.0 + 14.0) / 3.0
    assert registry.expected_drop("P5300-1") == pytest.approx(expected_mean, abs=1e-6)

    # Rejection of zero drop
    with pytest.raises(PackError):
        registry.record_flight("P5300-1", 50.0, 50.0)

    # Rejection of negative drop
    with pytest.raises(PackError):
        registry.record_flight("P5300-1", 50.0, 55.0)

    # Rejection of NaN drops
    with pytest.raises(PackError):
        registry.record_flight("P5300-1", float("nan"), 50.0)
    with pytest.raises(PackError):
        registry.record_flight("P5300-1", 50.0, float("nan"))

    # Mean remains completely unchanged
    assert registry.expected_drop("P5300-1") == pytest.approx(expected_mean, abs=1e-6)


def test_next_flight_allowed() -> None:
    """7. next_flight_allowed: allowed case returns (True, "ok");

    short rest -> reason starts "REST:"; low SoC -> "SOC:"; both -> "REST:";
    exactly at the gate -> allowed; a learned larger drop turns an allowed case into "SOC:";
    unknown pack and NaN voltage -> (False, reason starting "INPUT:").
    """
    registry = PackRegistry.load()

    # Allowed case: rest >= 60, high resting voltage
    allowed, reason = registry.next_flight_allowed("P5300-1", 16.0, 65.0)
    assert allowed is True
    assert reason == "ok"

    # Short rest -> reason starts "REST:"
    allowed, reason = registry.next_flight_allowed("P5300-1", 16.0, 45.0)
    assert allowed is False
    assert reason.startswith("REST:")

    # Low SoC -> reason starts "SOC:" (cooldown ok, but resting voltage low)
    # At 14.76 V on 4S (3.69 V/cell), predicted SoC is 10.0%. Post-flight: 10 - 10 = 0% < 30%.
    allowed, reason = registry.next_flight_allowed("P5300-1", 14.76, 65.0)
    assert allowed is False
    assert reason.startswith("SOC:")

    # Both fail -> reason starts "REST:"
    allowed, reason = registry.next_flight_allowed("P5300-1", 14.76, 45.0)
    assert allowed is False
    assert reason.startswith("REST:")

    # Exactly at the gate -> allowed
    # Cooldown = 60.0 s (== min_rest_s)
    # Voltage: 3.80 V/cell * 4 = 15.20 V -> SoC 40.0%. Drop = 10.0%. Post-flight: 40.0 - 10.0 = 30.0% == gate.
    allowed, reason = registry.next_flight_allowed("P5300-1", 15.20, 60.0)
    assert allowed is True
    assert reason == "ok"

    # A learned larger drop turns an allowed case into "SOC:"
    # Currently 15.20 V at 60s cooldown is allowed.
    # Record a flight with drop = 15.0%
    registry.record_flight("P5300-1", 50.0, 35.0)
    # New expected drop is (10.0? No, before records default was 10.0. With 1 record, drop is 15.0%).
    # Post-flight SoC = 40.0 - 15.0 = 25.0% < 30.0% gate.
    allowed, reason = registry.next_flight_allowed("P5300-1", 15.20, 60.0)
    assert allowed is False
    assert reason.startswith("SOC:")

    # Unknown pack -> (False, reason starting "INPUT:")
    allowed, reason = registry.next_flight_allowed("NON_EXISTENT", 16.0, 65.0)
    assert allowed is False
    assert reason.startswith("INPUT:")

    # NaN voltage -> (False, reason starting "INPUT:")
    allowed, reason = registry.next_flight_allowed("P5300-1", float("nan"), 65.0)
    assert allowed is False
    assert reason.startswith("INPUT:")


def test_sag_critical() -> None:
    """8. sag_critical: False just above 4 x 3.55 V, True just below, True for NaN, True for an unknown pack."""
    registry = PackRegistry.load()

    # 4 x 3.55 V = 14.20 V
    # False just above 4 x 3.55 V
    assert registry.sag_critical("P5300-1", 14.21) is False

    # True just below
    assert registry.sag_critical("P5300-1", 14.19) is True

    # True for NaN
    assert registry.sag_critical("P5300-1", float("nan")) is True

    # True for an unknown pack
    assert registry.sag_critical("UNKNOWN_PACK", 16.0) is True
