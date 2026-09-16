"""Subscribe preset helpers (Option B — pure host-side, no firmware changes).

Two modes are available in this module:

**Option A — Firmware preset IDs** (requires firmware support):
    Uses pre-defined variable sets stored in firmware flash, requested by a
    compact 11-byte frame. The firmware maps the ID to a range table internally.
    Not yet implemented in firmware; use Option B for now.

**Option B — Host-side preset merge** (active, this module):
    Merges variable lists from multiple presets (or custom sets) on the host,
    resolves them to DWARF addresses, and sends a standard dynamic-range
    0x21 subscribe request. No firmware changes required. The subscribe request
    is slightly larger than Option A but fully flexible.

    Usage::

        from ground_station.comm.subscribe_presets import PresetManager
        from ground_station.livewatch.symbols import SymbolResolver

        resolver = SymbolResolver("OBJ/JX_FLY.axf")
        pm = PresetManager(resolver)

        # Single preset at 50 Hz
        ranges, meta = pm.merge([PresetManager.PRESET_IMU_PID])
        req = pm.build_request(ranges, divider=4, slot=1, transport=1)
        sock.sendto(req, ("192.168.4.1", 14550))

        # Combine two presets into one slot
        ranges, meta = pm.merge([PresetManager.PRESET_BOOT_DEFAULT,
                                PresetManager.PRESET_IMU_PID])
        req = pm.build_request(ranges, divider=20, slot=2, transport=1)
        sock.sendto(req, ("192.168.4.1", 14550))

Preset IDs (mirrored from the Option A firmware spec, docs/subscribe-preset-architecture.md):

    0x01  boot-default  — Euler angles + DroneStatus; 5 ranges, 10 Hz default
    0x02  imu-pid       — gyro rate + Euler + inner-rate PID; 14 ranges, 50 Hz default
    0x03  mrac-full     — Theta + Whatf per axis; 16 ranges, 20 Hz default

Variable lists are defined as DWARF symbol paths. Size must be in {1, 2, 4} bytes
to match the subscribe protocol constraint, so Theta[0:5] is listed as 6 individual
indices: mrac_state.roll.Theta[0] ... Theta[5].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ground_station.livewatch.stream import (
    MAX_STREAM_RANGES,
    STREAM_MAX_BYTES,
    FRAME_OVERHEAD,
    SEND_TASK_HZ,
    BUDGET_PCT,
    TRANSPORT_USART3,
    build_stream_request,
    stream_bps,
)
from ground_station.livewatch.symbols import SymbolResolver
# LiveTransportError is what build_stream_request() actually raises via the
# host-side _validate() mirror; merge()'s docstring promises ValueError to its
# callers (notably build_burst_request's overflow fallback) so we convert
# here at the merge boundary instead of catching a second exception type in
# every consumer.
from ground_station.livewatch.transport import LiveTransportError


# =======================================================================
# Preset ID constants
# =======================================================================

PRESET_NONE          = 0x00   # reserved: not a valid preset request
PRESET_BOOT_DEFAULT  = 0x01
PRESET_IMU_PID      = 0x02
PRESET_MRAC_FULL    = 0x03

PRESET_NAMES: dict[int, str] = {
    PRESET_BOOT_DEFAULT: "boot-default",
    PRESET_IMU_PID:     "imu-pid",
    PRESET_MRAC_FULL:   "mrac-full",
}

# Default divider → Hz at 200 Hz Send_Task cadence
PRESET_DIVIDERS: dict[int, int] = {
    PRESET_BOOT_DEFAULT: 20,   # 10 Hz
    PRESET_IMU_PID:      4,   # 50 Hz
    PRESET_MRAC_FULL:    10,  # 20 Hz
}

# Expected range count per preset (for documentation / schema validation)
PRESET_RANGE_COUNTS: dict[int, int] = {
    PRESET_BOOT_DEFAULT:  5,
    PRESET_IMU_PID:     14,
    PRESET_MRAC_FULL:    16,
}


# =======================================================================
# Option A: compact 11-byte preset request (requires firmware support)
# Kept for completeness; wifi_bridge uses Option B.
# =======================================================================

_PRESET_MODE_MARKER = 0xFF
_STREAM_CMD = 0x21
_SYNC_HI, _SYNC_LO = 0xCC, 0xDE


def build_preset_request(
    preset_id: int,
    slot: int = 1,
    divider: Optional[int] = None,
    transport: int = TRANSPORT_USART3,
) -> bytes:
    """Build an 11-byte Option A preset request (firmware-side preset).

    DEPRECATED: firmware does not yet implement preset tables. Use
    PresetManager.merge() + PresetManager.build_request() instead.

    Args:
        preset_id: Firmware preset ID (1-3). Must not be 0x00.
        slot:      Subscribe slot index (0-3). Default 1.
        divider:   Rate divider. Default from PRESET_DIVIDERS.
        transport: 1 = USART3/WiFi, 0 = UART5.

    Returns:
        11-byte request frame.
    """
    if preset_id == 0 or preset_id > 15:
        raise ValueError(f"preset_id must be 1-15, got {preset_id:#04x}")
    if slot >= 4:
        raise ValueError(f"slot must be 0-3, got {slot}")
    if divider is None:
        divider = PRESET_DIVIDERS.get(preset_id, 20)

    payload_len = 4
    body = bytes([
        _STREAM_CMD, payload_len, _PRESET_MODE_MARKER,
        divider & 0xFF, transport & 0xFF, slot & 0xFF, preset_id & 0xFF,
    ])
    crc = 0
    for b in body:
        crc ^= b
    return bytes([_SYNC_HI, _SYNC_LO]) + body + bytes([crc])


# =======================================================================
# Option B: pure host-side preset merge
# =======================================================================

@dataclass(frozen=True)
class _PresetDef:
    """Variable names (DWARF paths) for one preset."""

    name: str
    divider: int          # recommended default divider
    doc: str
    vars: tuple[str, ...]


# ---- Preset variable definitions (DWARF paths) --------------------------------
#
# Each entry is a valid path as passed to SymbolResolver.resolve().
# Array elements are listed individually because the subscribe protocol requires
# size in {1, 2, 4} bytes — Theta[0:5] = 6×float32 = 24 B, which is not
# directly subscribable; the 6 individual indices are.
#
# Symbols verified against OBJ/JX_FLY.axf DWARF (live reads 2026-09-08).

_PRESETS: dict[int, _PresetDef] = {
    PRESET_BOOT_DEFAULT: _PresetDef(
        name="boot-default",
        divider=20,
        doc="Euler angles (roll/pitch/yaw) + arm status + flight mode. "
            "Mirrors the firmware's hardcoded boot-default slot. "
            "5 ranges, ~20 B payload, 10 Hz default.",
        vars=(
            "imu_data.rol",
            "imu_data.pit",
            "imu_data.yaw",
            "DroneStatus.ARM_Status",
            "DroneStatus.FlyMode",
        ),
    ),
    PRESET_IMU_PID: _PresetDef(
        name="imu-pid",
        divider=4,
        doc="Raw gyro rates (ORI_Gyro*) + Euler angles + inner-rate PID "
            "loop states (FB error, Kp, Ki, cumulative error) for roll and pitch. "
            "14 ranges, ~56 B payload, 50 Hz default. "
            "Tracks the signals MRAC uses to compute u_ad.",
        vars=(
            # Raw gyro rates, deg/s
            "ORI_Gyrox",
            "ORI_Gyroy",
            "ORI_Gyroz",
            # Euler angles
            "imu_data.rol",
            "imu_data.pit",
            "imu_data.yaw",
            # Roll inner-rate PID states
            "Ctrler.gyroxPID.FB",    # rate error
            "Ctrler.gyroxPID.Kp",
            "Ctrler.gyroxPID.Ki",
            "Ctrler.gyroxPID.E",    # cumulative error
            # Pitch inner-rate PID states
            "Ctrler.gyroyPID.FB",
            "Ctrler.gyroyPID.Kp",
            "Ctrler.gyroyPID.Ki",
            "Ctrler.gyroyPID.E",
        ),
    ),
    PRESET_MRAC_FULL: _PresetDef(
        name="mrac-full",
        divider=10,
        doc="MRAC adaptive weights (Theta[0:3]) and L1-filtered copies "
            "(Whatf[0:3]) for roll/pitch/yaw/z_rate axes. MAX_NUM_BASIS=4. "
            "16 ranges, ~64 B payload, 20 Hz default.",
        vars=(
            # Roll adaptive weights
            "mrac_state.roll.Theta[0]",
            "mrac_state.roll.Theta[1]",
            "mrac_state.roll.Theta[2]",
            "mrac_state.roll.Theta[3]",
            "mrac_state.roll.Whatf[0]",
            "mrac_state.roll.Whatf[1]",
            "mrac_state.roll.Whatf[2]",
            "mrac_state.roll.Whatf[3]",
            # Pitch adaptive weights
            "mrac_state.pitch.Theta[0]",
            "mrac_state.pitch.Theta[1]",
            "mrac_state.pitch.Theta[2]",
            "mrac_state.pitch.Theta[3]",
            "mrac_state.pitch.Whatf[0]",
            "mrac_state.pitch.Whatf[1]",
            "mrac_state.pitch.Whatf[2]",
            "mrac_state.pitch.Whatf[3]",
        ),
    ),
}


class PresetManager:
    """Host-side preset merge: merges variable lists and builds subscribe requests.

    Usage::

        resolver = SymbolResolver("OBJ/JX_FLY.axf")
        pm = PresetManager(resolver)

        # Single preset
        ranges, meta = pm.merge([PresetManager.PRESET_IMU_PID])
        req = pm.build_request(ranges, divider=4, slot=1, transport=1)
        wifi_sock.sendto(req, ("192.168.4.1", 14550))

        # Two presets merged into one slot
        ranges, meta = pm.merge([PresetManager.PRESET_BOOT_DEFAULT,
                                PresetManager.PRESET_IMU_PID])
        req = pm.build_request(ranges, divider=20, slot=2, transport=1)
        wifi_sock.sendto(req, ("192.168.4.1", 14550))

    The merged request uses the standard dynamic-range 0x21 format — no firmware
    changes required. The 0x08 schema reply names all merged variables.
    """

    # Class-level preset ID constants for clean call sites
    PRESET_NONE         = PRESET_NONE
    PRESET_BOOT_DEFAULT = PRESET_BOOT_DEFAULT
    PRESET_IMU_PID      = PRESET_IMU_PID
    PRESET_MRAC_FULL     = PRESET_MRAC_FULL

    def __init__(self, resolver: SymbolResolver):
        """Create a PresetManager.

        Args:
            resolver: SymbolResolver pointing at the firmware ELF. Used to
                translate variable names to subscribe protocol addresses.
                Pass None to skip resolution (for metadata-only inspection).
        """
        self._resolver = resolver

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def known_presets(self) -> list[int]:
        """List known preset IDs (0x01–0x03)."""
        return sorted(_PRESETS.keys())

    def preset_info(self, preset_id: int) -> dict:
        """Return preset metadata without resolving variables.

        Returns:
            Dict with keys: id, name, divider, hz, n_vars, doc, ranges.
            Empty dict for unknown preset_id.
        """
        defn = _PRESETS.get(preset_id)
        if not defn:
            return {}
        return {
            "id":      preset_id,
            "name":    defn.name,
            "divider": defn.divider,
            "hz":      round(SEND_TASK_HZ / defn.divider, 1),
            "n_vars":  len(defn.vars),
            "doc":     defn.doc,
            "ranges":  self._resolve_vars(defn.vars) if self._resolver else None,
        }

    def merge(
        self,
        preset_ids: list[int],
        slot: int = 1,
        divider: Optional[int] = None,
        transport: int = TRANSPORT_USART3,
        usart3_baud: int = 921600,
        other_bps: int = 0,
    ) -> tuple[list, "PresetMergeResult"]:
        """Resolve and merge one or more presets into a subscribe request.

        Args:
            preset_ids:  List of preset IDs to merge (e.g. [PRESET_IMU_PID] or
                        [PRESET_BOOT_DEFAULT, PRESET_MRAC_FULL]).
                        The merged set is sent as one slot's subscription.
            slot:       Subscribe slot index (0-3). Default 1.
            divider:    Rate divider. Default: divider of the first preset.
            transport:  1 = USART3/WiFi (default), 0 = UART5.
            usart3_baud: USART3 baud for bandwidth arithmetic. Default 921600.
            other_bps: Bytes/s already committed by other slots on this transport.
                        The bandwidth check sums this with the new request.

        Returns:
            (ranges, PresetMergeResult) where:
              - ranges:   list of StreamRange suitable for build_stream_request()
              - result:   PresetMergeResult metadata

        Raises:
            ValueError: Empty preset_ids, unknown preset, variable won't resolve,
                        exceeds MAX_STREAM_RANGES, exceeds STREAM_MAX_BYTES,
                        or bandwidth budget exceeded.
        """
        if not preset_ids:
            raise ValueError("preset_ids cannot be empty")

        if slot >= 4:
            raise ValueError(f"slot must be 0-3, got {slot}")

        # Deduplicate IDs while preserving order
        seen: set[int] = set()
        unique: list[int] = []
        for pid in preset_ids:
            if pid == PRESET_NONE:
                raise ValueError(
                    f"0x00 is PRESET_NONE and not a valid preset request"
                )
            if pid not in _PRESETS:
                raise ValueError(
                    f"unknown preset_id {pid:#04x}. "
                    f"Known: {sorted(_PRESETS)}"
                )
            if pid not in seen:
                seen.add(pid)
                unique.append(pid)

        # Default divider: first preset's recommended value
        if divider is None:
            divider = _PRESETS[unique[0]].divider

        # Collect all variable names (deduplicated by name)
        var_names: list[str] = []
        var_names_set: set[str] = set()
        for pid in unique:
            for name in _PRESETS[pid].vars:
                if name not in var_names_set:
                    var_names_set.add(name)
                    var_names.append(name)

        # Resolve to StreamRanges
        ranges = self._resolve_vars(var_names)
        if len(ranges) > MAX_STREAM_RANGES:
            raise ValueError(
                f"merged {len(ranges)} ranges exceeds protocol cap "
                f"{MAX_STREAM_RANGES} — drop a preset or reduce vars"
            )

        # Build and validate. build_stream_request() goes through the
        # host-side _validate() mirror which raises LiveTransportError on
        # range/payload/bandwidth overflow -- convert at this seam so the
        # docstring's ValueError contract holds for callers (specifically
        # build_burst_request()'s multi-slot fallback, which would otherwise
        # never see the overflow and stay in dead-code territory).
        try:
            request = build_stream_request(
                ranges,
                divider=divider,
                transport=transport,
                usart3_baud=usart3_baud,
                slot=slot,
                other_bps=other_bps,
            )
        except LiveTransportError as exc:
            raise ValueError(str(exc)) from exc

        # Compute result metadata
        total_bytes = sum(r.nbytes for r in ranges)
        bps = stream_bps(total_bytes, divider)
        allowed = (usart3_baud // 10) * BUDGET_PCT[transport] // 100
        available = allowed - other_bps
        budget_pct = round(100 * bps / allowed, 1) if allowed else 0.0

        # Preset name list
        names = [f"{pid:#04x}({_PRESETS[pid].name})" for pid in unique]
        result = PresetMergeResult(
            preset_ids=unique,
            preset_names=names,
            n_ranges=len(ranges),
            total_bytes=total_bytes,
            divider=divider,
            hz=round(SEND_TASK_HZ / divider, 1),
            transport=transport,
            bps=bps,
            budget_pct=budget_pct,
            available_bps=available,
        )

        return ranges, result

    def build_request(
        self,
        preset_ids: list[int],
        slot: int = 1,
        divider: Optional[int] = None,
        transport: int = TRANSPORT_USART3,
        usart3_baud: int = 921600,
        other_bps: int = 0,
    ) -> tuple[bytes, "PresetMergeResult"]:
        """Merge presets and build the wire request in one call.

        Convenience wrapper around merge() + build_stream_request().

        Returns:
            (request_bytes, PresetMergeResult)
        """
        ranges, result = self.merge(
            preset_ids, slot, divider, transport, usart3_baud, other_bps
        )
        return build_stream_request(
            ranges, divider=result.divider,
            transport=result.transport,
            usart3_baud=usart3_baud,
            slot=slot, other_bps=other_bps,
        ), result

    def build_burst_request(
        self,
        preset_ids: list[int],
        base_slot: int = 1,
        divider: Optional[int] = None,
        transport: int = TRANSPORT_USART3,
        usart3_baud: int = 921600,
        other_bps: int = 0,
    ) -> "BurstRequestResult":
        """Aggregate multiple presets into ONE subscription if they fit.

        The 0x21 protocol allows up to MAX_STREAM_RANGES ranges and
        STREAM_MAX_BYTES payload per subscription. When several presets'
        combined range list fits inside both caps, this method packs them all
        into a single slot's subscription so the FC emits one wider data
        frame per divider instead of one slot per preset. That is the
        telemetry-heavy case the spec calls out: a single rich frame carries
        more bytes per Send_Task cycle than several narrow frames, and it
        halves the per-cycle header overhead.

        When the combined set overflows either cap, the method falls back to
        a per-preset multi-slot allocation -- one preset per slot starting at
        base_slot -- which is the legacy behaviour. The caller can inspect
        ``strategy`` to know which path was taken.

        Args:
            preset_ids:  Preset IDs to aggregate (e.g.
                        [PRESET_BOOT_DEFAULT, PRESET_IMU_PID, PRESET_MRAC_FULL]).
            base_slot:   First slot index for the allocation. Default 1.
                         In burst mode this is the only slot the request
                         targets; in multi-slot fallback the i-th preset
                         goes to (base_slot + i) until MAX_SLOTS is reached.
            divider:     Rate divider. Default: the SLOWEST preset's
                         recommended value (max divider across the set) so
                         burst traffic does not overrun any preset's
                         intended cadence.
            transport:   1 = USART3/WiFi (default), 0 = UART5/wired.
            usart3_baud: USART3 baud for bandwidth arithmetic. Default 921600.
            other_bps:   BPS already committed by other slots on this transport.

        Returns:
            BurstRequestResult with ``strategy`` ('burst' or 'multi-slot'),
            ``requests`` as a list of ``(slot, bytes)`` tuples ready to send,
            and ``results`` aligned with the per-preset PresetMergeResult list.

        Raises:
            ValueError: Empty preset_ids, unknown preset, or merged ranges
                        overflow even a single subscription's cap AND the
                        preset count exceeds the available slot count.
        """
        if not preset_ids:
            raise ValueError("preset_ids cannot be empty")
        if base_slot < 0 or base_slot >= MAX_SLOTS:
            raise ValueError(f"base_slot must be 0..{MAX_SLOTS - 1}, got {base_slot}")

        # Deduplicate preset IDs while preserving order. Same rule as merge().
        seen: set[int] = set()
        unique: list[int] = []
        for pid in preset_ids:
            if pid == PRESET_NONE:
                raise ValueError("0x00 is PRESET_NONE and not a valid preset request")
            if pid not in _PRESETS:
                raise ValueError(
                    f"unknown preset_id {pid:#04x}. Known: {sorted(_PRESETS)}"
                )
            if pid not in seen:
                seen.add(pid)
                unique.append(pid)

        # Default divider: the slowest preset's recommended value (largest
        # divider across the set). That keeps burst traffic within every
        # bundled preset's intended cadence; the user can raise it for more
        # headroom or lower it to push the rate.
        if divider is None:
            divider = max(_PRESETS[pid].divider for pid in unique)

        # Try the one-shot merge first. merge() already validates ranges,
        # payload width, and bandwidth. The failure modes we catch here are:
        #   - payload overflow (combined > STREAM_MAX_BYTES = 1024 B)
        #   - range overflow (combined ranges > MAX_STREAM_RANGES = 62)
        #   - bandwidth overflow at the requested divider
        # In every case the fallback is per-preset on its own slot.
        try:
            ranges, merge_result = self.merge(
                unique, slot=base_slot, divider=divider,
                transport=transport, usart3_baud=usart3_baud,
                other_bps=other_bps,
            )
            request = build_stream_request(
                ranges, divider=merge_result.divider,
                transport=merge_result.transport,
                usart3_baud=usart3_baud, slot=base_slot,
                other_bps=other_bps,
            )
            return BurstRequestResult(
                preset_ids=unique,
                strategy="burst",
                requests=[(base_slot, request)],
                results=[merge_result],
                note="aggregated into a single slot",
            )
        except ValueError as exc:
            # Multi-slot fallback. Need at least len(unique) free slots
            # starting at base_slot. Last preset gets base_slot + N - 1;
            # that must be < MAX_SLOTS, otherwise we cannot allocate.
            if base_slot + len(unique) > MAX_SLOTS:
                raise ValueError(
                    f"burst fallback needs {len(unique)} slots starting at "
                    f"{base_slot}, but only {MAX_SLOTS - base_slot} are "
                    f"available (presets too many for one FC): {exc}"
                ) from exc

            requests: list[tuple[int, bytes]] = []
            results: list = []
            rolling_bps = other_bps
            for i, pid in enumerate(unique):
                slot = base_slot + i
                # Each preset's own divider is the right default once we are
                # back to one-preset-per-slot -- merge() will pick it.
                ranges, merge_result = self.merge(
                    [pid], slot=slot,
                    transport=transport, usart3_baud=usart3_baud,
                    other_bps=rolling_bps,
                )
                request = build_stream_request(
                    ranges, divider=merge_result.divider,
                    transport=merge_result.transport,
                    usart3_baud=usart3_baud, slot=slot,
                    other_bps=rolling_bps,
                )
                requests.append((slot, request))
                results.append(merge_result)
                if merge_result.bps:
                    rolling_bps += merge_result.bps

            return BurstRequestResult(
                preset_ids=unique,
                strategy="multi-slot",
                requests=requests,
                results=results,
                note=f"fallback because: {exc}",
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_vars(self, var_names: list[str]) -> list:
        """Resolve variable names to StreamRanges, packing adjacent scalars."""
        if not self._resolver:
            return []
        from ground_station.livewatch.stream import StreamRange

        syms = []
        for name in var_names:
            sym = self._resolver.resolve(name)
            syms.append(sym)

        # Sort by address
        syms.sort(key=lambda s: s.address)

        # Pack adjacent same-size scalars into one range
        gap_merge_bytes = 4
        ranges: list = []
        cur: list = []
        cur_size = None
        cur_end = None

        for sym in syms:
            if cur_size is None:
                cur = [sym]
                cur_size = sym.size
                cur_end = sym.address + sym.size
            elif (sym.size == cur_size
                  and 0 <= sym.address - cur_end < gap_merge_bytes
                  and sym.size in (1, 2, 4)):
                cur.append(sym)
                cur_end = sym.address + sym.size
            else:
                ranges.append(self._flush(cur))
                cur = [sym]
                cur_size = sym.size
                cur_end = sym.address + sym.size
        if cur:
            ranges.append(self._flush(cur))

        return [StreamRange(
            address=r[0].address,
            size=r[0].size,
            count=len(r),
            name=", ".join(s.name for s in r),
            fmt=r[0].fmt,
        ) for r in ranges]

    def _flush(self, syms: list) -> list:
        syms.sort(key=lambda s: s.address)
        return syms


@dataclass(frozen=True)
class PresetMergeResult:
    """Metadata from a preset merge operation.

    Attributes:
        preset_ids:    Preset IDs that were merged.
        preset_names:  Human-readable names with IDs.
        n_ranges:      Number of subscribe ranges in the request.
        total_bytes:  Data payload width in bytes.
        divider:       Rate divider used.
        hz:            Effective data rate in Hz.
        transport:     Transport (1=WiFi, 0=UART5).
        bps:           Bytes/s on the wire.
        budget_pct:   Percentage of link budget used.
        available_bps: Bytes/s remaining on this transport.
    """

    preset_ids: list[int]
    preset_names: list[str]
    n_ranges: int
    total_bytes: int
    divider: int
    hz: float
    transport: int
    bps: int
    budget_pct: float
    available_bps: int


@dataclass(frozen=True)
class BurstRequestResult:
    """Result of PresetManager.build_burst_request().

    Attributes:
        preset_ids: Preset IDs that were bundled.
        strategy:   'burst' if the merged set fit into one slot, 'multi-slot'
                    if the call fell back to per-preset slot allocation.
        requests:   List of ``(slot, request_bytes)`` tuples. In 'burst' mode
                    this has exactly one entry; in 'multi-slot' mode one entry
                    per preset. Each request is ready to send to the FC.
        results:    One PresetMergeResult per element of ``requests``. For
                    'burst' this is the single merged subscription's metadata;
                    for 'multi-slot' each entry covers one preset's slot.
        note:       Free-form human-readable explanation. For 'burst' this is
                    a confirmation; for 'multi-slot' it carries the original
                    ValueError message so the caller knows why the fallback
                    fired.
    """

    preset_ids: list[int]
    strategy: str
    requests: list
    results: list
    note: str
