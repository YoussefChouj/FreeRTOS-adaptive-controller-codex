"""Self-describing session block (WP-42 P3): what a recording needs to be read back without the code that wrote it.

A recording directory (storage.CsvRecorder) is telemetry.csv + manifest.json + events.jsonl. This module builds the
``session_schema`` block of manifest.json at recording start and reads a recording back:

  format, format_version   "gs-session", 2
  build        ground-station git commit (+ dirty flag) and the firmware ELF (path, size, mtime, sha256)
  contracts    firmware_contract versions (CONTRACT_VERSION, GS_PROTO_VERSION, PLATFORM_COMMAND_VERSION, slot limits)
  tunables     every *_ROW cell of the firmware tables (tools/row_meta.py): file, table, param, unit, min, max, value.
               These are the source defaults of the checkout; live changes are the parameter commands below.
  variables    one entry per subscribed telemetry variable: name, slot, type, count, unit, rate_hz, divider, address

Parameter commands are already events: core._record_event mirrors every command lifecycle step into events.jsonl as
kind "command" (id, idx, value, lifecycle, outcome). ``read`` returns them with the schema.

Format choice: ULog-like (definitions header + data + event stream in one self-contained recording), kept on the
existing directory layout rather than MCAP. MCAP would add a dependency (not installed) and a binary container to a
pipeline whose loaders (flightlab, log_corpus, flight_report) all read the CSV + manifest; the long CSV already copes
with a key set that changes mid-session. The block is additive, so an MCAP exporter can be written from it later.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from ground_station.platform.firmware_contract import (CONTRACT_VERSION, GS_PROTO_VERSION, PLATFORM_COMMAND_VERSION,
                                                     SUBSCRIBE_MAX_SLOTS, SUBSCRIBE_SEND_TASK_HZ)

FORMAT, FORMAT_VERSION = "gs-session", 2
REPO = Path(__file__).resolve().parents[2]
FMT_TYPE = {"f": "float32", "d": "float64", "b": "int8", "B": "uint8", "h": "int16", "H": "uint16",
            "i": "int32", "I": "uint32", "q": "int64", "Q": "uint64", "?": "bool"}


def contracts() -> dict[str, Any]:
    return {"contract": CONTRACT_VERSION, "gs_proto": GS_PROTO_VERSION, "platform_command": PLATFORM_COMMAND_VERSION,
            "subscribe_max_slots": SUBSCRIBE_MAX_SLOTS, "subscribe_send_task_hz": SUBSCRIBE_SEND_TASK_HZ}


def git_build(repo: Path = REPO) -> dict[str, Any] | None:
    """The checkout's commit and whether the tree is dirty; None outside a git checkout."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                                timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
                               capture_output=True, text=True, timeout=10, check=True).stdout.strip() != ""
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": dirty}


def firmware_elf(path: Path) -> dict[str, Any] | None:
    """Path, size, mtime and sha256 of the ELF; None when it does not exist (never a fake path)."""
    try:
        st = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return {"path": path.as_posix(), "size": int(st.st_size), "mtime": float(st.st_mtime), "sha256": digest}


def tunables(files: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    """Every firmware *_ROW cell (tools/row_meta.py --json); cells of out-of-range rows are still listed."""
    spec = importlib.util.spec_from_file_location("row_meta", REPO / "tools" / "row_meta.py")
    if spec is None or spec.loader is None:
        return []
    row_meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(row_meta)
    paths = list(files) if files is not None else sorted(
        list((REPO / "API").glob("*.c")) + list((REPO / "TASK").glob("*.c")))
    cells: list[dict[str, Any]] = []
    for p in paths:
        row_meta.check_file(Path(p).resolve(), cells)
    return cells


def variables(schemas: Mapping[int, Any], units: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """One entry per variable of the accepted subscriptions (livewatch.stream.StreamSchema per slot)."""
    units = units or {}
    out: list[dict[str, Any]] = []
    for slot in sorted(schemas):
        s = schemas[slot]
        rate = round(float(s.hz), 3)
        for r in s.ranges:
            names = r._names or (r.name or f"0x{r.address:08X}",)
            fmts = r._fmts or (r.fmt,) * len(names)
            count = 1 if r._names else r.count
            for i, (name, fmt) in enumerate(zip(names, fmts)):
                out.append({"name": name, "slot": int(slot), "type": FMT_TYPE.get((fmt or "")[-1:], "raw"),
                            "count": count, "unit": units.get(name), "rate_hz": rate, "divider": int(s.divider),
                            "address": f"0x{r.address + i * r.size:08X}"})
    return out


def describe(*, build: dict[str, Any] | None, elf: dict[str, Any] | None, variables: list[dict[str, Any]],
             tunables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"format": FORMAT, "format_version": FORMAT_VERSION,
            "build": {"git": build, "firmware_elf": elf},
            "contracts": contracts(), "tunables": tunables or [], "variables": variables}


def read(session_dir: str | Path) -> dict[str, Any]:
    """The schema block, the parameter-command events and the other events of a recording directory."""
    d = Path(session_dir)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = []
    path = d / "events.jsonl"
    if path.exists():
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"schema": manifest.get("session_schema"),
            "params": [e for e in events if e.get("kind") == "command"],
            "events": events}
