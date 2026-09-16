"""Named logging manifests: a variable set + a sample rate + a unique CSV.

A manifest is the unit an operator or an agent asks for by name ("log ekf_vs_of at
50 Hz"). It resolves to DWARF symbol paths, so any firmware variable is loggable
with no firmware change and no reflash -- the ELF is the only contract.

Rate feasibility is CHECKED, not assumed. The probe is bandwidth-limited, so a
variable set has a hard ceiling on sample rate; asking for more than that would
otherwise produce a CSV that silently logs slower than its filename claims, which
is precisely the kind of error that survives into analysis. `feasibility()` models
it offline and `calibrate()` measures it on real hardware before logging starts.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .registry import Registry

_DEFAULT_MANIFESTS = Path(__file__).with_name("manifests.yaml")
_PRESETS_FILE = Path(__file__).with_name("multi_slot_presets.yaml")

# Sync contract: every multi-slot preset's union of slot manifests MUST cover
# these vars, otherwise post-flight analysis cannot correlate slot data with
# arm/disarm transitions. The capture tool refuses to load a preset that
# misses any of these.
#
# Rationale:
#   ARM_Status   -- discrete arm/disarm event, needs to mark log sections
#   FlyMode      -- flight mode at each tick (SDK / AltHold / ...)
#   real_voltage -- battery sag during the run
#   xTickCount   -- monotonic ms clock for cross-slot time alignment
#
# Verified against OBJ/JX_FLY.axf (live reads 2026-09-11).
REQUIRED_SYNC_VARS: tuple[str, ...] = (
    "DroneStatus.ARM_Status",
    "DroneStatus.FlyMode",
    "real_voltage",
    "xTickCount",
)

@dataclass(frozen=True)
class CostModel:
    ms_per_region: float
    ms_per_byte: float
    transport_name: str
    basis: str

    def describe(self) -> str:
        kbps = 1.0 / self.ms_per_byte
        return (f"{self.ms_per_region:.2f} ms/region + {self.ms_per_byte:.3f} ms/B, "
                f"sample at ~{kbps:.1f} KB/s ({self.basis})")


@dataclass
class Manifest:
    name: str
    vars: list[str]
    hz: float = 20.0
    doc: str = ""

    @property
    def slug(self) -> str:
        """Filename-safe stem; manifest names come from YAML keys or the CLI."""
        return "".join(c if (c.isalnum() or c in "-_") else "_" for c in self.name)


class ManifestStore:
    """Loads manifests.yaml. Missing file is fine -- ad-hoc manifests still work."""

    def __init__(self, path: str | Path | None = None, registry: Registry | None = None):
        self.path = Path(path) if path else _DEFAULT_MANIFESTS
        self.registry = registry or Registry()
        data = {}
        if self.path.exists():
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
        self._m = data.get("manifests", {})

    def names(self) -> list[str]:
        return sorted(self._m)

    def get(self, name: str) -> Manifest:
        if not name:
            raise KeyError(f"no manifest given; use one of {self.names()} or --vars")
        if name not in self._m:
            raise KeyError(f"no manifest {name!r}; have {self.names()}")
        spec = self._m[name]
        return Manifest(
            name=name,
            vars=self.registry.expand(list(spec.get("vars", []))),
            hz=float(spec.get("hz", 20.0)),
            doc=spec.get("doc", ""),
        )

    def adhoc(self, tokens: list[str], hz: float, name: str = "adhoc") -> Manifest:
        return Manifest(name=name, vars=self.registry.expand(tokens), hz=hz)


# ---------------------------------------------------------------------------
# Rate feasibility
# ---------------------------------------------------------------------------

@dataclass
class Feasibility:
    n_vars: int
    n_regions: int
    n_bytes: int
    sample_ms: float
    max_hz: float
    measured: bool = False

    def ok_for(self, hz: float, tol: float = 1.05) -> bool:
        return hz <= self.max_hz * tol

    def describe(self) -> str:
        how = "measured" if self.measured else "estimated"
        return (f"{self.n_vars} vars / {self.n_regions} region(s) / {self.n_bytes} B "
                f"-> {self.sample_ms:.2f} ms per sample, max {self.max_hz:.0f} Hz ({how})")


def feasibility(plan, cost_model: CostModel) -> Feasibility:
    """Offline estimate from a transport's cost model. No hardware needed."""
    n_bytes = sum(r.size for r in plan.regions)
    n_regions = len(plan.regions)
    ms = (cost_model.ms_per_region * n_regions
          + cost_model.ms_per_byte * n_bytes)
    return Feasibility(len(plan.symbols), n_regions, n_bytes, ms, 1000.0 / ms, measured=False)


def calibrate(reader, plan, cost_model: CostModel, n: int = 15) -> Feasibility:
    """Measure the real per-sample cost against the attached target.

    Uses the median, not the mean: the probe emits occasional multi-hundred-ms
    outliers on USB retries, and a mean would let one of those understate the
    achievable rate badly.
    """
    base = feasibility(plan, cost_model=cost_model)
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        reader.sample(plan)
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    ms = times[len(times) // 2]
    return Feasibility(base.n_vars, base.n_regions, base.n_bytes, ms, 1000.0 / ms, measured=True)


# ---------------------------------------------------------------------------
# Output naming
# ---------------------------------------------------------------------------

def unique_csv_path(outdir: str | Path, manifest: Manifest, hz: float,
                    when: float | None = None) -> Path:
    """<outdir>/<slug>_<hz>hz_<YYYYmmdd-HHMMSS>.csv, collision-suffixed.

    The rate is in the name because the same manifest logged at different rates
    produces datasets that must never be confused for one another.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))
    stem = f"{manifest.slug}_{hz:g}hz_{stamp}"
    path = outdir / f"{stem}.csv"
    n = 2
    while path.exists():
        path = outdir / f"{stem}_{n}.csv"
        n += 1
    return path


def _elf_fingerprint(elf: str | Path) -> dict:
    p = Path(elf)
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None
    return {"path": str(p), "sha256_16": h,
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime))
            if p.exists() else None}


def write_meta(csv_path: Path, manifest: Manifest, plan, elf: str | Path,
               requested_hz: float, feas: Feasibility, extra: dict | None = None) -> Path:
    """Sidecar JSON describing exactly how this CSV was produced.

    The symbol addresses are only meaningful against the build they came from, so
    the ELF fingerprint is recorded alongside them; a log analysed against the
    wrong firmware would otherwise be silently misinterpreted.
    """
    meta = {
        "manifest": {"name": manifest.name, "doc": manifest.doc,
                     "vars": manifest.vars, "hz": manifest.hz},
        "requested_hz": requested_hz,
        "feasibility": {"n_vars": feas.n_vars, "n_regions": feas.n_regions,
                        "n_bytes": feas.n_bytes, "sample_ms": round(feas.sample_ms, 3),
                        "max_hz": round(feas.max_hz, 1), "measured": feas.measured},
        "symbols": [{"name": s.name, "addr": f"0x{s.address:08X}", "size": s.size}
                    for s in plan.symbols],
        "regions": [{"start": f"0x{r.start:08X}", "size": r.size} for r in plan.regions],
        "elf": _elf_fingerprint(elf),
        "transport": "swd-cmsis-dap",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        meta.update(extra)
    path = csv_path.with_suffix(".meta.json")
    path.write_text(json.dumps(meta, indent=2))
    return path


# ---------------------------------------------------------------------------
# Multi-slot budget calculation
# ---------------------------------------------------------------------------

# USART3 WiFi wire capacity (921600 baud, 8N1 = 10 bits per byte).
USART3_WIRE_BPS = 92160  # 921600 / 10

# Firmware-published data-frame overhead, mirrored from API/subscribe.h:
#   6 B header + 4 B source timestamp + 2 B CRC16 = 12 B per data frame.
# The budget must agree with what Subscribe_BuildStreamFrame actually emits,
# otherwise the guard mis-sizes (over- or under-rejects). See the comment on
# compute_multi_slot_budget() for the full derivation.
DATA_FRAME_OVERHEAD_B = 12

# Conservative 80% of the wire for the host-side check. The firmware's own
# guard sits at 95% (USART3 has 5% unallocatable margin per the TX-ring
# rework comment); 80% is the host "leave headroom for jitter" threshold.
HOST_SAFE_WIRE_PCT = 80.0

# Host assumes 4-byte (float32) value cells. Every shipped manifest uses
# float32 throughout; if a future manifest mixes uint16/float32 the budget
# will over-estimate slightly, which is the safe direction. Set False for
# any var where the firmware data-frame payload per range is not size*count.
DEFAULT_VALUE_BYTES = 4


@dataclass
class SlotBudget:
    """Per-slot budget breakdown."""
    slot: int
    manifest_name: str
    num_ranges: int
    hz: float
    frame_size_bytes: int
    bps: float
    wire_pct: float


@dataclass
class MultiSlotBudget:
    """Aggregate budget across all enabled slots."""
    total_bps: float
    wire_pct: float
    per_slot: list[SlotBudget]
    safe: bool  # True if wire_pct <= HOST_SAFE_WIRE_PCT

    def summary(self) -> str:
        """One-line summary for UI display."""
        status = "✓ SAFE" if self.safe else "⚠ OVERBUDGET"
        return f"{self.total_bps / 1000:.1f} KB/s ({self.wire_pct:.1f}% wire) {status}"


def compute_multi_slot_budget(
    slot_configs: list[dict],
    store: ManifestStore | None = None,
    send_task_hz: int = 200,
    value_bytes: int = DEFAULT_VALUE_BYTES,
) -> MultiSlotBudget:
    """Compute wire budget for multiple subscribe slots.

    Wire budget for each slot is the per-slot data-frame size (firmware
    overhead + value bytes) times the actual emission rate. The per-slot
    data frame, as built by ``API/subscribe.c::Subscribe_BuildStreamFrame``,
    consists of:

      6 B header (sync_hi, sync_lo, frame_type, len_hi, len_lo, seq)
      4 B source timestamp (T_MS, xTaskGetTickCount at sample time)
      N * size * count value bytes (no per-range address overhead --
          the schema went out once in the 0x08 reply)
      2 B CRC16-CCITT (XModem)

    Total per data frame = 12 + N * size * count bytes.

    Emission rate = SEND_TASK_HZ / divider. The host builds
    ``divider = int(send_task_hz / hz)``, so the firmware's budget guard
    sees ``bps = frame_size * send_task_hz / int(send_task_hz / hz)``.
    ``send_task_hz`` defaults to 200 (matches ``SUBSCRIBE_SEND_TASK_HZ``
    in API/subscribe.h). Note: the firmware constant is the *nominal*
    rate; the actual MIXED-mode Send_Task cadence is ~80 Hz (see the
    2026-09-12-wire-budget investigation). The host's 200 Hz figure is
    what the budget guard reasons against -- it errs toward rejecting
    early, which is the safe direction for the wire check.

    Args:
        slot_configs: List of dicts with keys:
            - enabled: bool (required)
            - slot: int (required)
            - manifest: str (manifest name, required if enabled)
            - hz: float (required if enabled)
        store: ManifestStore instance (defaults to global manifests.yaml)
        send_task_hz: firmware Send_Task rate the budget arithmetic mirrors.
            Defaults to 200 (matches API/subscribe.h constant).
        value_bytes: assumed bytes per value cell. Defaults to 4 (float32),
            which is what every shipped manifest uses.

    Returns:
        MultiSlotBudget with per-slot breakdown and total wire %.
    """
    if store is None:
        store = ManifestStore()

    total_bps = 0.0
    per_slot = []

    for cfg in slot_configs:
        if not cfg.get("enabled", False):
            continue

        manifest_name = cfg["manifest"]
        hz = float(cfg["hz"])
        slot = int(cfg["slot"])

        # Load manifest and count vars (each var -> one range, size=4 count=1
        # for every shipped manifest). Total payload = n_vars * value_bytes.
        manifest = store.get(manifest_name)
        num_ranges = len(manifest.vars)

        # Data frame: 12 B overhead + payload bytes. The previous formula
        # used 10 + num_ranges * 8 (request-frame format), which conflated
        # request encoding with data-frame encoding and over-estimated the
        # wire cost by ~65% for a typical manifest.
        frame_size = DATA_FRAME_OVERHEAD_B + num_ranges * value_bytes

        # The host emits divider = int(send_task_hz / hz). The firmware's
        # budget guard mirrors this exactly, so bps is the same number the
        # drone sees.
        divider = max(1, int(send_task_hz / hz))
        bps = frame_size * send_task_hz / divider
        wire_pct = (bps / USART3_WIRE_BPS) * 100.0

        total_bps += bps
        per_slot.append(SlotBudget(
            slot=slot,
            manifest_name=manifest_name,
            num_ranges=num_ranges,
            hz=hz,
            frame_size_bytes=frame_size,
            bps=bps,
            wire_pct=wire_pct
        ))

    total_wire_pct = (total_bps / USART3_WIRE_BPS) * 100.0
    safe = total_wire_pct <= HOST_SAFE_WIRE_PCT

    return MultiSlotBudget(
        total_bps=total_bps,
        wire_pct=total_wire_pct,
        per_slot=per_slot,
        safe=safe
    )


# ---------------------------------------------------------------------------
# Multi-slot preset management
# ---------------------------------------------------------------------------

class MultiSlotPresetManager:
    """Load and save multi-slot logging presets."""
    
    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path(__file__).with_name("multi_slot_presets.yaml")
        self.path = Path(path)
        self._presets: dict = {}
        self._load()
    
    def _load(self) -> None:
        """Load presets from YAML file."""
        if not self.path.exists():
            self._presets = {}
            return
        
        with open(self.path, "r") as f:
            data = yaml.safe_load(f) or {}
        
        self._presets = data.get("presets", {})
    
    def list_presets(self) -> list[str]:
        """Return sorted list of preset names."""
        return sorted(self._presets.keys())
    
    def get(self, name: str, validate_sync_contract: bool = True) -> dict:
        """Get preset config by name.

        Args:
            name: Preset name.
            validate_sync_contract: If True (default), verify the preset's union
                of slot manifests covers REQUIRED_SYNC_VARS. Raises ValueError
                with a clear remediation message if any required var is missing.
                Set False only for tooling that loads presets for editing/
                inspection (e.g. the dashboard's preset editor) before the user
                has finished composing them.

        Returns:
            Dict with keys:
                - description: str
                - slots: list[dict] with keys slot/manifest/hz
        """
        if name not in self._presets:
            raise KeyError(f"Preset {name!r} not found; available: {self.list_presets()}")
        preset = self._presets[name]
        if validate_sync_contract:
            self._enforce_sync_contract(name, preset)
        return preset

    def _enforce_sync_contract(self, preset_name: str, preset: dict) -> None:
        """Refuse to return a preset whose manifest union misses any sync var.

        The capture pipeline relies on every preset having arm/flymode/voltage/
        tick available somewhere in the slot union, so post-flight analysis can
        correlate slot data with arm/disarm transitions. This is Level-1 error
        prevention: the bad case cannot exist, by construction.
        """
        store = ManifestStore()  # uses default manifests.yaml
        slots = preset.get("slots", []) or []
        covered: set[str] = set()
        for slot_cfg in slots:
            manifest_name = slot_cfg.get("manifest")
            if not manifest_name:
                raise ValueError(
                    f"preset {preset_name!r}: slot {slot_cfg.get('slot')!r} has no "
                    f"'manifest' field; each slot must name a manifest from "
                    f"manifests.yaml")
            try:
                manifest = store.get(manifest_name)
            except KeyError as exc:
                raise ValueError(
                    f"preset {preset_name!r}: slot {slot_cfg.get('slot')!r} "
                    f"references unknown manifest {manifest_name!r} ({exc})")
            covered.update(manifest.vars)
        missing = [v for v in REQUIRED_SYNC_VARS if v not in covered]
        if missing:
            raise ValueError(
                f"preset {preset_name!r} is missing required sync vars "
                f"({', '.join(missing)}). Add the 'sync' manifest to one of its "
                f"slots, or replace an existing slot's manifest with one that "
                f"covers all four vars. Required: {list(REQUIRED_SYNC_VARS)}")
    
    def save(self, name: str, description: str, slots: list[dict]) -> None:
        """Save a new preset or overwrite existing.
        
        Args:
            name: Preset name (slug-safe)
            description: Human-readable description
            slots: List of dicts with keys slot/manifest/hz
        """
        self._presets[name] = {
            "description": description,
            "slots": slots
        }
        
        # Write back to YAML
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            yaml.dump({"presets": self._presets}, f, default_flow_style=False, sort_keys=False)
    
    def delete(self, name: str) -> None:
        """Delete a preset."""
        if name in self._presets:
            del self._presets[name]
            with open(self.path, "w") as f:
                yaml.dump({"presets": self._presets}, f, default_flow_style=False, sort_keys=False)
