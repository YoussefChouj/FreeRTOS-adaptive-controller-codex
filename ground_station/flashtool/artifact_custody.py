"""Snapshot / restore the OBJ/JX_FLY.{axf,hex,map} triple around a build.

A rebuild that is not subsequently flashed leaves the working tree holding
artifacts that no longer describe the firmware on the target. livewatch's
DWARF resolution trusts the local ``.axf``; a stale one returns
plausible-looking garbage rather than an error — a float is a float whatever
it points at. The 2026-07-26 12-byte ``.bss`` drift silently invalidated two
pinned test goldens, and the same hazard would silently poison any
livewatch-driven decision until the next flash.

The custody protocol:

1. ``snapshot()`` copies the triple into ``.flashtool-cache/`` before UV4
   starts touching ``OBJ/``. The cache directory is gitignored via the
   project-wide ``OBJ/`` exclusion; no per-file entry is needed.
2. ``commit()`` deletes the cache directory — the running target now matches
   the on-disk artifacts, so custody is satisfied.
3. ``restore()`` puts the snapshot back byte-exact and is invoked whenever a
   build completes without a flash (abandoned builds, infrastructure-only
   ``build`` invocations, any pipeline crash mid-way).

The cache path lives under ``OBJ/`` so the file moves stay local — copying
across drives would slow the build for no benefit and risk the snapshot
itself becoming stale if the cache drive has a clock skew.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


CACHE_DIRNAME = ".flashtool-cache"

#: The triple that describes "what is on the target". All three are needed:
#: ``.axf`` is what DWARF reads, ``.hex`` is what the firmware on the target
#: was programmed from, ``.map`` is what humans consult for crash triage.
FLASHED_TRIPLE = ("JX_FLY.axf", "JX_FLY.hex", "JX_FLY.map")


@dataclass(frozen=True)
class CustodyState:
    """Result of a custody operation — enough info to decide the next step."""

    snapped: bool             # True iff snapshot() actually wrote the cache
    triple_present: bool      # True iff all three flashed-triple files exist now
    cache_path: Path | None   # path to the cache directory if a snapshot exists
    reasons: list[str]        # populated on partial / failed operations


def cache_dir(obj_dir: str | Path) -> Path:
    """Location of the custody cache directory under ``OBJ/``."""
    return Path(obj_dir) / CACHE_DIRNAME


def snapshot(obj_dir: str | Path) -> CustodyState:
    """Copy the flashed triple into the cache directory.

    Idempotent: re-snapshotting over an existing cache overwrites the
    cached triple with whatever is currently in ``OBJ/``. This is what we
    want — the cache always reflects the "previous build's" artifacts,
    even across repeated invocations.
    """
    obj_dir = Path(obj_dir)
    cache = cache_dir(obj_dir)
    cache.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for name in FLASHED_TRIPLE:
        src = obj_dir / name
        if not src.exists():
            missing.append(f"{src} missing — nothing to snapshot")
            continue
        shutil.copy2(src, cache / name)
    return CustodyState(
        snapped=True,
        triple_present=all((obj_dir / n).exists() for n in FLASHED_TRIPLE),
        cache_path=cache,
        reasons=missing,
    )


def commit(obj_dir: str | Path) -> CustodyState:
    """Delete the cache — the running target now matches on-disk artifacts."""
    cache = cache_dir(obj_dir)
    if cache.exists():
        shutil.rmtree(cache)
    return CustodyState(
        snapped=False,
        triple_present=all((Path(obj_dir) / n).exists() for n in FLASHED_TRIPLE),
        cache_path=None,
        reasons=[],
    )


def restore(obj_dir: str | Path) -> CustodyState:
    """Put the cached triple back, byte-exact, then delete the cache.

    A no-op (with an explanatory reason) when no snapshot exists, so a
    standalone call from a hook always returns a sensible state rather
    than raising.
    """
    obj_dir = Path(obj_dir)
    cache = cache_dir(obj_dir)
    reasons: list[str] = []
    if not cache.exists():
        reasons.append("no cache present — restore is a no-op")
        return CustodyState(
            snapped=False,
            triple_present=all((obj_dir / n).exists() for n in FLASHED_TRIPLE),
            cache_path=None,
            reasons=reasons,
        )
    restored: list[str] = []
    missing_in_cache: list[str] = []
    for name in FLASHED_TRIPLE:
        cached = cache / name
        if not cached.exists():
            missing_in_cache.append(name)
            continue
        shutil.copy2(cached, obj_dir / name)
        restored.append(name)
    shutil.rmtree(cache)
    if missing_in_cache:
        reasons.append(
            f"cache was incomplete; only restored {restored}; "
            f"missing {missing_in_cache}"
        )
    return CustodyState(
        snapped=False,
        triple_present=all((obj_dir / n).exists() for n in FLASHED_TRIPLE),
        cache_path=None,
        reasons=reasons,
    )


def has_snapshot(obj_dir: str | Path) -> bool:
    """Is there an active custody snapshot for ``obj_dir``?"""
    cache = cache_dir(obj_dir)
    return cache.exists() and any((cache / n).exists() for n in FLASHED_TRIPLE)