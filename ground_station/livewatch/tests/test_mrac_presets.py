"""Counted-array manifests and the `mrac_theta_phi` subscribe preset.

`mrac_state.<axis>.Theta[]` / `.Phi[]` are sized by MRAC_CAPACITY, so a static YAML list
cannot say "the first N elements". A manifest token `{dwarf: <array>, count_from: <const>}`
expands to `<array>[0..N-1]` with N read from the firmware ELF. These tests cover that
mechanism and the shipped preset built on it.

No hardware. The mechanism tests use a scratch manifests.yaml and the real
OBJ/JX_FLY.axf (skipped, with the reason, if it lacks the MRAC descriptor).
"""
from pathlib import Path

import pytest

from ground_station.comm.manifest_layer import resolve_ranges_from_names
from ground_station.livewatch import cli
from ground_station.livewatch.manifest import (
    ManifestStore, MultiSlotPresetManager, compute_multi_slot_budget,
)
from ground_station.livewatch.mrac_features import read_mrac_n_features
from ground_station.livewatch.symbols import SymbolResolver

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"
AXES = ("pitch", "roll", "yaw", "z_rate")
PRESET = "mrac_theta_phi"
# Subscribe-frame overhead: 6 B header + 4 B timestamp + 2 B CRC (API/subscribe.c).
FRAME_OVERHEAD = 12


@pytest.fixture(scope="module")
def n_features():
    if not ELF.exists():
        pytest.skip(f"{ELF} not present")
    try:
        return read_mrac_n_features(ELF)
    except (KeyError, ValueError, TypeError) as exc:
        pytest.skip(f"{ELF.name} has no mrac_n_features (predates S2a?): {exc}")


@pytest.fixture(scope="module")
def store(n_features):
    return ManifestStore(elf_path=ELF)


@pytest.fixture(scope="module")
def resolver(n_features):
    r = SymbolResolver(ELF)
    yield r
    r.close()


def _scratch_store(tmp_path, body: str, elf_path=ELF) -> ManifestStore:
    path = tmp_path / "manifests.yaml"
    path.write_text("manifests:\n" + body, encoding="utf-8")
    return ManifestStore(path=path, elf_path=elf_path)


# ---- the shipped manifests -------------------------------------------------------

@pytest.mark.parametrize("name, field", [("mrac_theta", "Theta"), ("mrac_phi", "Phi")])
def test_manifest_lists_every_axis_and_every_live_element(store, n_features, name, field):
    m = store.get(name)
    assert m.vars == [f"mrac_state.{ax}.{field}[{i}]"
                      for ax in AXES for i in range(n_features)]


@pytest.mark.parametrize("name", ["mrac_theta", "mrac_phi"])
def test_manifest_resolves_to_one_contiguous_range_per_axis(store, resolver, n_features, name):
    """Elements of one axis are adjacent floats, so each axis costs one stream range."""
    m = store.get(name)
    ranges = list(resolve_ranges_from_names(m.vars, resolver))
    assert len(ranges) == len(AXES)
    assert all(r.size == 4 and r.count == n_features for r in ranges)


def test_slot_frame_size_follows_the_feature_count(store, n_features):
    """Budget: 12 B overhead + 4 axes * N floats, whatever N the build has."""
    preset = MultiSlotPresetManager().get(PRESET)
    slots = [{"enabled": True, "slot": s["slot"], "manifest": s["manifest"], "hz": s["hz"]}
             for s in preset["slots"]]
    budget = compute_multi_slot_budget(slots, store=store)
    assert budget.safe
    by_name = {sb.manifest_name: sb for sb in budget.per_slot}
    for name in ("mrac_theta", "mrac_phi"):
        assert by_name[name].frame_size_bytes == FRAME_OVERHEAD + len(AXES) * n_features * 4


# ---- the preset --------------------------------------------------------------------

def test_preset_loads_without_reading_the_elf(monkeypatch):
    """The sync contract never needs Theta/Phi, so a missing build cannot block the preset."""
    def refuse(self, name):
        raise AssertionError("the sync-contract check must not open the ELF")
    monkeypatch.setattr(ManifestStore, "_open_elf", refuse)
    preset = MultiSlotPresetManager().get(PRESET)
    assert [(s["slot"], s["manifest"]) for s in preset["slots"]] == [
        (0, "sync"), (1, "mrac_theta"), (2, "mrac_phi")]


# ---- the mechanism -----------------------------------------------------------------

def test_expand_counted_false_drops_the_counted_tokens(store):
    assert store.get("mrac_theta", expand_counted=False).vars == []


def test_plain_manifests_never_need_an_elf(tmp_path):
    store = ManifestStore(elf_path=tmp_path / "absent.axf")
    assert store.get("sync").vars    # the shipped sync manifest is all scalars


def test_scalar_and_counted_tokens_keep_their_order(tmp_path, n_features):
    store = _scratch_store(tmp_path, (
        "  mixed:\n"
        "    hz: 10\n"
        "    vars:\n"
        "      - xTickCount\n"
        "      - { dwarf: mrac_state.roll.Theta, count_from: mrac_n_features }\n"
        "      - real_voltage\n"))
    assert store.get("mixed").vars == (
        ["xTickCount"]
        + [f"mrac_state.roll.Theta[{i}]" for i in range(n_features)]
        + ["real_voltage"])


def test_a_missing_elf_is_a_clear_error_naming_the_manifest(tmp_path):
    store = _scratch_store(tmp_path, (
        "  counted:\n"
        "    vars:\n"
        "      - { dwarf: mrac_state.roll.Theta, count_from: mrac_n_features }\n"),
        elf_path=tmp_path / "absent.axf")
    with pytest.raises(ValueError, match=r"'counted'.*absent\.axf"):
        store.get("counted")


def test_a_file_that_is_not_an_elf_is_a_clear_error(tmp_path):
    junk = tmp_path / "junk.axf"
    junk.write_bytes(b"not an elf")
    store = _scratch_store(tmp_path, (
        "  counted:\n"
        "    vars:\n"
        "      - { dwarf: mrac_state.roll.Theta, count_from: mrac_n_features }\n"),
        elf_path=junk)
    with pytest.raises(ValueError, match="cannot be read"):
        store.get("counted")


@pytest.mark.parametrize("token", [
    "{ dwarf: no_such_array, count_from: mrac_n_features }",         # unknown array
    "{ dwarf: mrac_state.roll.Theta, count_from: no_such_count }",   # unknown count
    "{ dwarf: mrac_state.roll.Theta, count_from: mrac_state.roll }", # count is not a scalar
    "{ count_from: mrac_n_features }",                               # no array path
])
def test_a_bad_counted_token_is_a_clear_error(tmp_path, n_features, token):
    store = _scratch_store(tmp_path, f"  bad:\n    vars:\n      - {token}\n")
    with pytest.raises(ValueError, match="'bad'"):
        store.get("bad")


def test_expansion_is_cached_across_manifests(tmp_path, n_features, monkeypatch):
    """Two manifests naming the same array open the ELF once per get(), and reuse the result."""
    store = _scratch_store(tmp_path, (
        "  a:\n    vars:\n      - { dwarf: mrac_state.roll.Phi, count_from: mrac_n_features }\n"
        "  b:\n    vars:\n      - { dwarf: mrac_state.roll.Phi, count_from: mrac_n_features }\n"))
    first = store.get("a").vars
    monkeypatch.setattr(ManifestStore, "_open_elf",
                        lambda self, name: pytest.fail("cache miss: ELF reopened"))
    assert store.get("b").vars == first


# ---- CLI --------------------------------------------------------------------------

def test_manifests_command_lists_counted_manifests_with_their_size(n_features, capsys):
    assert cli.main(["--elf", str(ELF), "manifests"]) in (0, None)
    out = capsys.readouterr().out
    for name in ("mrac_theta", "mrac_phi"):
        line = next(ln for ln in out.splitlines() if ln.startswith(name))
        assert f"{len(AXES) * n_features:>3} vars" in line


def test_manifests_command_survives_an_unreadable_elf(tmp_path, capsys):
    """One unsizeable manifest must not hide the others."""
    cli.main(["--elf", str(tmp_path / "absent.axf"), "manifests"])
    out = capsys.readouterr().out
    assert "sync" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("mrac_theta"))
    assert "?" in line and "absent.axf" in line
