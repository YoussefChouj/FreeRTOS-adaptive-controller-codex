"""Offline tests for the logging-manifest layer.

No hardware. The cost model, output naming and store are pure; the plan-dependent
parts run against the real firmware ELF like the rest of the suite.
"""
import json
import time
from pathlib import Path

import pytest

from ground_station.livewatch.manifest import (
    CostModel, Feasibility, Manifest, ManifestStore, MultiSlotPresetManager,
    REQUIRED_SYNC_VARS, calibrate, feasibility, unique_csv_path, write_meta,
)
from ground_station.livewatch.reader import Plan, Region, build_plan
from ground_station.livewatch.symbols import SymbolResolver
from ground_station.livewatch.transport import SwdCmsisDap

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"
SWD_COST = SwdCmsisDap.cost_model


# ---- cost model (pure) -------------------------------------------------

def _plan(regions, n_syms=1):
    return Plan(symbols=[None] * n_syms, regions=[Region(a, s) for a, s in regions])


def test_feasibility_counts_regions_and_bytes():
    f = feasibility(_plan([(0x20000000, 16), (0x20001000, 32)], n_syms=5), cost_model=SWD_COST)
    assert f.n_regions == 2
    assert f.n_bytes == 48
    assert f.sample_ms == pytest.approx(
        2 * SWD_COST.ms_per_region + 48 * SWD_COST.ms_per_byte)
    assert f.max_hz == pytest.approx(1000.0 / f.sample_ms)
    assert f.measured is False


def test_scattered_vars_cost_more_than_adjacent_ones():
    """Per-region cost is why a manifest should prefer neighbouring fields."""
    same_bytes_one_region = feasibility(_plan([(0x20000000, 64)]), cost_model=SWD_COST)
    same_bytes_four_regions = feasibility(_plan(
        [(0x20000000 + i * 0x1000, 16) for i in range(4)]),
        cost_model=SWD_COST)
    assert same_bytes_four_regions.sample_ms > same_bytes_one_region.sample_ms
    assert same_bytes_four_regions.max_hz < same_bytes_one_region.max_hz


def test_ok_for_has_small_tolerance_but_rejects_real_overshoot():
    f = Feasibility(n_vars=1, n_regions=1, n_bytes=4, sample_ms=10.0, max_hz=100.0)
    assert f.ok_for(100.0)
    assert f.ok_for(104.0)      # within the 5 % tolerance band
    assert not f.ok_for(150.0)


def test_calibrate_uses_median_not_mean():
    """One USB stall must not be allowed to understate the achievable rate."""
    class FakeReader:
        def __init__(self):
            self.n = 0

        def sample(self, plan):
            self.n += 1
            if self.n == 1:
                time.sleep(0.05)     # single large outlier
            else:
                time.sleep(0.001)

    f = calibrate(FakeReader(), _plan([(0x20000000, 8)]),
                  cost_model=SWD_COST, n=9)
    assert f.measured is True
    assert f.sample_ms < 20.0        # the 50 ms outlier did not dominate


# ---- output naming (pure) ----------------------------------------------

def test_unique_csv_path_encodes_manifest_and_rate(tmp_path):
    m = Manifest(name="of_drift", vars=["a"], hz=50)
    p = unique_csv_path(tmp_path, m, 50)
    assert p.parent == tmp_path
    assert p.name.startswith("of_drift_50hz_")
    assert p.suffix == ".csv"


def test_unique_csv_path_never_overwrites(tmp_path):
    m = Manifest(name="x", vars=["a"])
    when = time.time()
    first = unique_csv_path(tmp_path, m, 10, when=when)
    first.write_text("")
    second = unique_csv_path(tmp_path, m, 10, when=when)   # same timestamp
    assert second != first
    assert not second.exists()


def test_slug_sanitises_adhoc_names():
    assert Manifest(name="a b/c:d", vars=[]).slug == "a_b_c_d"


# ---- store -------------------------------------------------------------

def test_store_lists_and_expands_groups():
    s = ManifestStore()
    assert "ekf_ba_check" in s.names()
    m = s.get("ekf_vs_of")
    # 'group:ekf' must have been expanded into real DWARF paths, not passed through.
    assert not any(v.startswith("group:") for v in m.vars)
    assert "s_ekf.x[3]" in m.vars
    assert m.hz == 50


def test_store_rejects_unknown_and_empty_names():
    s = ManifestStore()
    with pytest.raises(KeyError):
        s.get("does_not_exist")
    with pytest.raises(KeyError):
        s.get("")


def test_adhoc_manifest_expands_group_tokens():
    s = ManifestStore()
    m = s.adhoc(["group:ekf", "s_of_bias_x"], hz=30, name="probe")
    assert m.name == "probe"
    assert m.hz == 30
    assert "s_of_bias_x" in m.vars
    assert len(m.vars) > 1


def test_missing_manifest_file_is_not_fatal(tmp_path):
    s = ManifestStore(path=tmp_path / "nope.yaml")
    assert s.names() == []
    assert s.adhoc(["s_of_bias_x"], hz=10).vars == ["s_of_bias_x"]


# ---- metadata sidecar (needs the ELF for real symbol addresses) --------

@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_write_meta_records_addresses_and_elf_fingerprint(tmp_path):
    res = SymbolResolver(ELF)
    try:
        m = ManifestStore().get("of_drift")
        plan = build_plan(res, m.vars)
        feas = feasibility(plan, cost_model=SWD_COST)
        csv_path = tmp_path / "of_drift_50hz_x.csv"
        meta_path = write_meta(csv_path, m, plan, ELF, requested_hz=50, feas=feas,
                               extra={"logged_hz": 50})
        meta = json.loads(meta_path.read_text())
    finally:
        res.close()

    assert meta_path.name.endswith(".meta.json")
    assert meta["manifest"]["name"] == "of_drift"
    assert meta["logged_hz"] == 50
    assert meta["transport"] == "swd-cmsis-dap"
    # An address-resolved log is only interpretable against the build it came
    # from, so the fingerprint must be present.
    assert meta["elf"]["sha256_16"]
    assert len(meta["symbols"]) == len(m.vars)
    assert all(s["addr"].startswith("0x") for s in meta["symbols"])


@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_shipped_manifests_all_resolve_and_fit_their_rate():
    """Every manifest must resolve against the ELF and be feasible as shipped."""
    res = SymbolResolver(ELF)
    store = ManifestStore()
    try:
        for name in store.names():
            m = store.get(name)
            plan = build_plan(res, m.vars)
            feas = feasibility(plan, cost_model=SWD_COST)
            assert feas.ok_for(m.hz), (
                f"manifest {name} asks for {m.hz} Hz but its ceiling is {feas.max_hz:.0f} Hz")
    finally:
        res.close()


# ---- multi-slot preset sync contract -----------------------------------
#
# Every shipped preset MUST cover REQUIRED_SYNC_VARS in the union of its slot
# manifests. This is the rule that stopped 2026-09-11's MRAC run from
# correlating arm/disarm transitions with the captured MRAC weights.
# `sync` is the canonical manifest that satisfies it; this test catches anyone
# who edits multi_slot_presets.yaml and forgets to add it.

def test_required_sync_vars_are_known_firmware_symbols():
    """The contract names real firmware variables, not aspirational ones."""
    assert "DroneStatus.ARM_Status" in REQUIRED_SYNC_VARS
    assert "DroneStatus.FlyMode" in REQUIRED_SYNC_VARS
    assert "real_voltage" in REQUIRED_SYNC_VARS      # NOT vbat -- the var is real_voltage
    assert "xTickCount" in REQUIRED_SYNC_VARS


def test_sync_manifest_covers_the_contract():
    """The canonical 'sync' manifest is the intended way to satisfy the contract."""
    store = ManifestStore()
    m = store.get("sync")
    for v in REQUIRED_SYNC_VARS:
        assert v in m.vars, f"sync manifest missing required var {v}"


def test_shipped_presets_all_satisfy_the_sync_contract():
    """Every entry in multi_slot_presets.yaml must load via .get() without raising.

    This is the regression guard for 2026-09-11: a new preset that doesn't
    include sync (or an equivalent) cannot ship, because the capture tool
    refuses it before any subscription is sent.
    """
    pm = MultiSlotPresetManager()
    assert pm.list_presets(), "no presets loaded -- multi_slot_presets.yaml missing?"
    for name in pm.list_presets():
        preset = pm.get(name)
        assert "slots" in preset
        # Verify the union actually contains the vars (not just that loading
        # succeeded on a buggy contract).
        store = ManifestStore()
        covered = set()
        for slot in preset["slots"]:
            covered.update(store.get(slot["manifest"]).vars)
        missing = [v for v in REQUIRED_SYNC_VARS if v not in covered]
        assert not missing, f"preset {name!r} fails sync contract: {missing}"


def test_get_refuses_preset_missing_sync_vars():
    """A preset that drops the sync manifest must raise ValueError on load."""
    pm = MultiSlotPresetManager()
    pm._presets["_broken_no_sync"] = {
        "description": "test: missing sync",
        "slots": [
            {"slot": 0, "manifest": "mrac_signals", "hz": 50},
            {"slot": 1, "manifest": "mrac_weights", "hz": 50},
        ],
    }
    try:
        with pytest.raises(ValueError, match="required sync vars"):
            pm.get("_broken_no_sync")
        # Preset editor / inspector can bypass to load half-composed presets.
        loaded = pm.get("_broken_no_sync", validate_sync_contract=False)
        assert len(loaded["slots"]) == 2
    finally:
        del pm._presets["_broken_no_sync"]


def test_get_rejects_slot_with_no_manifest():
    pm = MultiSlotPresetManager()
    pm._presets["_incomplete"] = {"slots": [{"slot": 0, "hz": 50}]}
    try:
        with pytest.raises(ValueError, match="no 'manifest' field"):
            pm.get("_incomplete")
    finally:
        del pm._presets["_incomplete"]


def test_get_rejects_slot_referencing_unknown_manifest():
    pm = MultiSlotPresetManager()
    pm._presets["_unknown"] = {
        "slots": [{"slot": 0, "manifest": "does_not_exist", "hz": 50}],
    }
    try:
        with pytest.raises(ValueError, match="unknown manifest"):
            pm.get("_unknown")
    finally:
        del pm._presets["_unknown"]
