"""Tests for the flight_test_adaptive preset and analysis signals mapping.

Verifies:
  1. The preset loads via MultiSlotPresetManager without raising.
  2. The wire budget stays at or below 1600 B/s (UART5 drop-free ceiling).
  3. Every DWARF name in every manifest slot resolves against the ELF
     (skipped when the ELF is absent).
  4. The analysis flight_signals.yaml is loadable and references valid keys.

No hardware needed.
"""
import pytest
import yaml
from pathlib import Path

from ground_station.livewatch.manifest import (
    ManifestStore,
    MultiSlotPresetManager,
    compute_multi_slot_budget,
)
from ground_station.livewatch.symbols import SymbolResolver

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"
FLIGHT_SIGNALS_YAML = (
    Path(__file__).resolve().parents[2]
    / "analysis"
    / "flight_signals.yaml"
)


# ---------------------------------------------------------------------------
# 1. Preset loads
# ---------------------------------------------------------------------------

def test_flight_test_adaptive_preset_loads():
    """flight_test_adaptive must load via .get() without raising ValueError."""
    pm = MultiSlotPresetManager()
    preset = pm.get("flight_test_adaptive")
    assert preset["description"]
    slots = preset["slots"]
    assert len(slots) == 4, "preset must have exactly 4 slots"


def test_flight_test_adaptive_preset_slot_order_and_manifests():
    """Slots 0-3 map to the expected manifest names."""
    pm = MultiSlotPresetManager()
    preset = pm.get("flight_test_adaptive")
    expected = [
        ("dashboard_frame_a", 40),
        ("inner_loops", 80),
        ("flight_test_outer", 50),
        ("flight_test_position", 20),
    ]
    for i, (manifest_name, hz) in enumerate(expected):
        slot = preset["slots"][i]
        assert slot["slot"] == i, "slot {} slot number mismatch".format(i)
        assert slot["manifest"] == manifest_name
        assert slot["hz"] == hz, "slot {} hz mismatch".format(i)


def test_flight_test_adaptive_satisfies_sync_contract():
    """Every REQUIRED_SYNC_VARS must be covered by the slot union."""
    pm = MultiSlotPresetManager()
    preset = pm.get("flight_test_adaptive")
    store = ManifestStore()
    covered = set()
    for slot in preset["slots"]:
        covered.update(store.get(slot["manifest"]).vars)
    from ground_station.livewatch.manifest import REQUIRED_SYNC_VARS
    missing = [v for v in REQUIRED_SYNC_VARS if v not in covered]
    assert not missing, "sync contract missing: {}".format(missing)


# ---------------------------------------------------------------------------
# 2. Budget check
# ---------------------------------------------------------------------------

def test_flight_test_adaptive_budget_fits_wifi_link():
    """The preset streams over WiFi (USART3); the host budget guard must pass."""
    pm = MultiSlotPresetManager()
    preset = pm.get("flight_test_adaptive")
    store = ManifestStore()
    slots = [
        {
            "enabled": True,
            "slot": s["slot"],
            "manifest": s["manifest"],
            "hz": s["hz"],
        }
        for s in preset["slots"]
    ]
    budget = compute_multi_slot_budget(slots, store=store)
    assert budget.safe, "budget {:.0f} B/s ({:.1f}% of wire) not safe".format(
        budget.total_bps, budget.wire_pct)


def test_flight_test_adaptive_budget_per_slot_positive():
    """Each slot must produce positive B/s (non-empty manifests)."""
    pm = MultiSlotPresetManager()
    preset = pm.get("flight_test_adaptive")
    store = ManifestStore()
    slots = [
        {
            "enabled": True,
            "slot": s["slot"],
            "manifest": s["manifest"],
            "hz": s["hz"],
        }
        for s in preset["slots"]
    ]
    budget = compute_multi_slot_budget(slots, store=store)
    for sb in budget.per_slot:
        assert sb.bps > 0, "slot {} ({}) has 0 B/s".format(sb.slot, sb.manifest_name)


# ---------------------------------------------------------------------------
# 3. DWARF resolution
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_flight_test_adaptive_all_names_resolve():
    """Every DWARF path in every manifest slot must resolve against the ELF."""
    res = SymbolResolver(ELF)
    store = ManifestStore()
    pm = MultiSlotPresetManager()
    preset = pm.get("flight_test_adaptive")
    missing = []
    for slot in preset["slots"]:
        m = store.get(slot["manifest"])
        for v in m.vars:
            try:
                res.resolve(v)
            except KeyError as e:
                missing.append((slot["manifest"], v, str(e)))
    res.close()
    if missing:
        lines = []
        for manifest, var, err in missing:
            lines.append("  {}/{}: {}".format(manifest, var, err))
        pytest.fail(
            "{} DWARF path(s) did not resolve:\n{}".format(
                len(missing), "\n".join(lines)
            )
        )


# ---------------------------------------------------------------------------
# 4. Analysis signals YAML
# ---------------------------------------------------------------------------

def test_flight_signals_yaml_loads():
    """flight_signals.yaml must be valid YAML with string values."""
    assert FLIGHT_SIGNALS_YAML.exists(), "{} not found".format(FLIGHT_SIGNALS_YAML)
    with open(FLIGHT_SIGNALS_YAML, "r") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    for key, val in data.items():
        assert val is None or isinstance(val, str), (
            "key {} has value {} (expected str or null)".format(key, val)
        )


def test_flight_signals_keys_reference_slot_prefixes():
    """Every non-null key in flight_signals.yaml must start with slot<N>."""
    assert FLIGHT_SIGNALS_YAML.exists()
    with open(FLIGHT_SIGNALS_YAML, "r") as f:
        data = yaml.safe_load(f)
    for key, val in data.items():
        if val is None:
            continue
        assert val.startswith("slot0.") or val.startswith(
            "slot1."
        ) or val.startswith("slot2.") or val.startswith("slot3."), (
            "key {} value {} does not start with slot<N>.".format(key, val)
        )
