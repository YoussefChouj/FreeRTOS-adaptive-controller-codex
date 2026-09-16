"""Offline tests for artifact custody.

The custody protocol operates on the on-disk ``OBJ/JX_FLY.{axf,hex,map}``
triple. The tests construct a synthetic ``OBJ/`` in a tmpdir, populate it
with a known triple, and exercise snapshot / restore / commit over it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ground_station.flashtool import artifact_custody


@pytest.fixture
def obj_dir(tmp_path: Path) -> Path:
    """A fresh ``OBJ/`` with a known triple populated."""
    d = tmp_path / "OBJ"
    d.mkdir()
    payloads = {
        "JX_FLY.axf": b"\x7fELF-axf-bytes",
        "JX_FLY.hex": b":10000000AABBCCDDEEFF00112233445566\n",
        "JX_FLY.map": b"Image Map - JX_FLY\n",
    }
    for name, body in payloads.items():
        (d / name).write_bytes(body)
    return d


def _hash(obj_dir: Path, name: str) -> str:
    return hashlib.sha256((obj_dir / name).read_bytes()).hexdigest()


# ---- snapshot ------------------------------------------------------------

def test_snapshot_copies_triple_into_cache(obj_dir: Path):
    before = {n: _hash(obj_dir, n) for n in artifact_custody.FLASHED_TRIPLE}
    state = artifact_custody.snapshot(obj_dir)
    assert state.snapped
    assert state.cache_path == artifact_custody.cache_dir(obj_dir)
    for name in artifact_custody.FLASHED_TRIPLE:
        cached = state.cache_path / name
        assert cached.exists()
        assert hashlib.sha256(cached.read_bytes()).hexdigest() == before[name]


def test_snapshot_reports_missing_files(obj_dir: Path):
    (obj_dir / "JX_FLY.hex").unlink()
    state = artifact_custody.snapshot(obj_dir)
    assert any("JX_FLY.hex" in r for r in state.reasons)
    # The two that DO exist still get cached.
    assert (state.cache_path / "JX_FLY.axf").exists()


def test_snapshot_is_idempotent(obj_dir: Path):
    """A second snapshot overwrites the cache with whatever is in OBJ/ now —
    important because we want the cache to always reflect "the previous
    build's artifacts", even across repeated invocations."""
    artifact_custody.snapshot(obj_dir)
    (obj_dir / "JX_FLY.axf").write_bytes(b"new axf content")
    artifact_custody.snapshot(obj_dir)
    cached = artifact_custody.cache_dir(obj_dir) / "JX_FLY.axf"
    assert cached.read_bytes() == b"new axf content"


# ---- restore --------------------------------------------------------------

def test_restore_restores_byte_exact(obj_dir: Path):
    before = {n: (obj_dir / n).read_bytes() for n in artifact_custody.FLASHED_TRIPLE}
    artifact_custody.snapshot(obj_dir)
    # Build "happened" — the on-disk files change.
    (obj_dir / "JX_FLY.axf").write_bytes(b"fresh build")
    (obj_dir / "JX_FLY.hex").write_bytes(b":00000001FF\n")
    (obj_dir / "JX_FLY.map").write_bytes(b"fresh map\n")

    state = artifact_custody.restore(obj_dir)
    for name, original in before.items():
        assert (obj_dir / name).read_bytes() == original
    assert not state.cache_path or not state.cache_path.exists()


def test_restore_is_noop_without_snapshot(obj_dir: Path):
    """Calling restore with no cache should be a graceful no-op, not an exception."""
    before = (obj_dir / "JX_FLY.axf").read_bytes()
    state = artifact_custody.restore(obj_dir)
    assert (obj_dir / "JX_FLY.axf").read_bytes() == before
    assert any("no cache" in r for r in state.reasons)


def test_restore_handles_partial_cache(obj_dir: Path):
    """If the cache is incomplete (a previous run crashed mid-restore), we
    restore what we have and complain about what we don't."""
    cache = artifact_custody.cache_dir(obj_dir)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "JX_FLY.axf").write_bytes(b"previous-build-axf")
    # hex and map deliberately absent in the cache.

    (obj_dir / "JX_FLY.axf").write_bytes(b"current-build-axf")
    state = artifact_custody.restore(obj_dir)
    assert (obj_dir / "JX_FLY.axf").read_bytes() == b"previous-build-axf"
    assert any("missing" in r for r in state.reasons)


# ---- commit ---------------------------------------------------------------

def test_commit_deletes_cache(obj_dir: Path):
    artifact_custody.snapshot(obj_dir)
    assert artifact_custody.cache_dir(obj_dir).exists()
    artifact_custody.commit(obj_dir)
    assert not artifact_custody.cache_dir(obj_dir).exists()


def test_commit_is_noop_without_cache(obj_dir: Path):
    """Commit on a clean tree must not raise."""
    artifact_custody.commit(obj_dir)   # does not raise


# ---- has_snapshot ---------------------------------------------------------

def test_has_snapshot_truthy_when_cache_present(obj_dir: Path):
    assert not artifact_custody.has_snapshot(obj_dir)
    artifact_custody.snapshot(obj_dir)
    assert artifact_custody.has_snapshot(obj_dir)


def test_has_snapshot_falsey_for_empty_cache_dir(obj_dir: Path):
    """An empty cache dir (created but no triple inside) is not a snapshot."""
    artifact_custody.cache_dir(obj_dir).mkdir()
    assert not artifact_custody.has_snapshot(obj_dir)


# ---- end-to-end custody scenario -----------------------------------------

def test_abandoned_build_leaves_triple_unchanged(obj_dir: Path):
    """Spec user-story #20: an abandoned build must leave the flashed-matching
    triple in place. We exercise it by running snapshot then restore — the
    canonical 'build did not finish, do not trust the new artifacts' path."""
    before = {n: (obj_dir / n).read_bytes() for n in artifact_custody.FLASHED_TRIPLE}
    artifact_custody.snapshot(obj_dir)
    # Simulate a build that produced different artifacts but never flashed.
    (obj_dir / "JX_FLY.axf").write_bytes(b"uncommitted build output")
    (obj_dir / "JX_FLY.hex").write_bytes(b":uncommitted\n")
    artifact_custody.restore(obj_dir)
    for name, original in before.items():
        current = (obj_dir / name).read_bytes()
        assert current == original, f"{name} drifted after abandoned build"


def test_successful_flash_releases_cache(obj_dir: Path):
    """After a flash, the on-disk artifacts describe what's on the target —
    custody is satisfied; the cache is deleted."""
    artifact_custody.snapshot(obj_dir)
    (obj_dir / "JX_FLY.axf").write_bytes(b"flashed build")
    (obj_dir / "JX_FLY.hex").write_bytes(b":flashed\n")
    artifact_custody.commit(obj_dir)
    assert (obj_dir / "JX_FLY.axf").read_bytes() == b"flashed build"
    assert not artifact_custody.has_snapshot(obj_dir)