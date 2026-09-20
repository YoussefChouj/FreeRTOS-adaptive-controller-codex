"""CLI multi-slot subscribe capture.

Mirrors the dashboard's `_multislot_run` logic so an agent can drive a
multi-slot capture from a shell without launching the GUI.

Usage:
    python -m ground_station.livewatch.capture_preset <preset_name> [--secs N]
                                                          [--outdir PATH]
                                                          [--elf PATH]

Example:
    python -m ground_station.livewatch.capture_preset flight_comprehensive --secs 12

The script lives in ground_station/livewatch/ so the standard `python -m`
form works (the dashboard's other tools -- `verify`, `watch`, `stream_log`
-- all run this way too).

What it does (same proven pattern as the dashboard):
    1. Pre-clear all 4 FC slots (3 rounds of stop commands) -- kills stale
       subscription state from any previous capture.
    2. Open an ephemeral UDP socket (no bind -- FC replies to source port).
    3. Send subscribe requests SEQUENTIALLY, 100 ms apart, waiting for the
       0x08 schema reply for slot N before sending slot N+1.
    4. Decode 0x09 data frames with MultiStreamDecoder.
    5. Write one CSV per slot under <outdir> with columns
       sample_idx,tick,data_hex (matches dashboard format so the existing
       multislot-analyze pipeline reads it unchanged).
    6. Print final sample count + measured Hz per slot.

The captured CSVs work with `python -m ground_station.scripts.multislot_analyze`.

Wire budget: the script runs `compute_multi_slot_budget()` on every enabled
slot before opening the FC socket and refuses to start if the configured
preset would exceed 80% of the USART3 link capacity. Frame-size arithmetic
mirrors `API/subscribe.c::Subscribe_BuildStreamFrame` (12 B overhead +
N*size*count value bytes); see the journal at
`.agent_contracts/2026-09-12-wire-budget/` for the half-rate diagnosis that
motivated this guard.

NOTE on measured-vs-requested rate: with the firmware-side fix landed
(API/subscribe.h `SUBSCRIBE_SEND_TASK_HZ`; the real Send_Task cadence is
100 Hz), the host uses 100 in
`int(100 / hz)` below so the divider sent to firmware matches what the
firmware's per-slot counter expects. Measured rates therefore land at
roughly `100 / divider`, which equals the requested Hz (within the
integer-truncation error: e.g. `int(100/30) = 3` rounds 30 Hz up to 33 Hz).
"""

from __future__ import annotations

import argparse
import csv
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Repo-relative imports -- run via `python .cursor/skills/capture_preset.py`
from ground_station.livewatch.manifest import (
    ManifestStore,
    MultiSlotPresetManager,
    compute_multi_slot_budget,
)
from ground_station.livewatch.stream import (
    DATA_FRAME,
    ERROR_FRAME,
    SCHEMA_FRAME,
    StreamSchema,
    build_stream_request,
    pop_frame,
    MultiStreamDecoder,
)
from ground_station.livewatch.symbols import SymbolResolver
from ground_station.comm.manifest_layer import resolve_ranges_from_names


# ---------------------------------------------------------------------------
# Pre-clear protocol
# ---------------------------------------------------------------------------

# Timing constants. Tuned on the 2026-09-13 bench + cold-boot fixture:
#   * NUDGE_GRACE  = 100 ms  -- between nudge and stop requests; the FC's
#     Subscribe_StreamTick is on a 200 Hz cycle, so a single 5 ms slot
#     catches most races; 100 ms gives 20 chances.
#   * STOP_GAP     =  20 ms  -- gap between per-slot stops so Send_Task
#     fully completes the previous one. Below 5 ms the FC occasionally
#     drops a request on USART3 ring-pressure.
#   * ROUND_GAP    = 300 ms  -- gap between rounds so the FC's Subscribe_Rx
#     task drains the ring and the host can pull any pending acks.
#   * DRAIN_FLOOR  = 500 ms  -- minimum drain, even if 0 frames seen. Keeps
#     preclear_fc_subscriptions() cheap on a quiet FC.
# Round count: 3 (legacy default) reliably clears cold-boot stale state. We
# bump to 5 here because the subscriber-from-Scope-B bench reproducibly
# races once per ~6 boots in 2026-09-13 stress tests; 5 rounds leaves no
# observed tail. Set to 3 for tight CI loops.
DEFAULT_PRE_CLEAR_ROUNDS = 5
DEFAULT_PRE_CLEAR_DRAIN_S = 2.5


@dataclass
class PreClearResult:
    """Outcome of one pre-clear run, suitable for printing or asserting."""

    drained_packets: int
    frame_types_seen: dict
    rounds: int
    duration_s: float
    finished_clean: bool

    def as_dict(self) -> dict:
        return {
            "drained_packets": self.drained_packets,
            "frame_types_seen": dict(self.frame_types_seen),
            "rounds": self.rounds,
            "duration_s": round(self.duration_s, 3),
            "finished_clean": self.finished_clean,
        }


def preclear_fc_subscriptions(
    host: str = "192.168.4.1",
    port: int = 14550,
    slots=range(4),
    rounds: int = DEFAULT_PRE_CLEAR_ROUNDS,
    drain_secs: float = DEFAULT_PRE_CLEAR_DRAIN_S,
    socket_factory=None,
    sleep=None,
) -> dict:
    """Reliably empty the FC's subscribe slots before a fresh subscription.

    Idempotent: safe to call twice; the second call simply confirms a clean
    state and returns ``finished_clean=True`` after one round of zero traffic.

    Args:
        host:        WiFi AP IP (default MicoAir factory).
        port:        UDP port (14550).
        slots:       iterable of slot indices to stop. ``range(4)`` covers
                     the firmware's MAX_SLOTS=4 by default.
        rounds:      number of stop-then-drain cycles. 3 clears typical
                     stale state; 5 absorbs cold-boot stress-test races.
        drain_secs:  total wall-clock drain budget. Each round gets
                     ``drain_secs / rounds`` to settle.
        socket_factory: callable returning a ``socket.socket`` -- test seam.
                        Defaults to ``socket.socket(AF_INET, SOCK_DGRAM)``
                        with ``timeout=0.1``.
        sleep:       test seam for ``time.sleep``. Defaults to ``time.sleep``.

    Returns:
        dict with keys:
            drained_packets  (int)  -- bytes received during drain windows.
            frame_types_seen (dict) -- byte3 -> count, for diagnostics.
            rounds           (int)  -- actual rounds executed.
            duration_s       (float)-- wall-clock seconds the call took.
            finished_clean   (bool) -- True if NO data frames (0x09-0x0C)
                                       seen in the FINAL drain window.

    Why rounds: the firmware's Send_Task may still be mid-tx when the host
    sends a stop; one stop can race the firmware's tx and miss. Multiple
    rounds with drains between them close the race. The 2026-09-13 cold-boot
    capture that motivated this routine drained 246 stale packets across
    3 rounds -- proof that the runtime state was leaking across captures.
    """
    if sleep is None:
        sleep = time.sleep

    owns_sock = False
    if socket_factory is None:
        owns_sock = True

        def _default_factory():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            return s

        socket_factory = _default_factory

    sock = socket_factory()
    start = time.monotonic()
    drained = 0
    frame_types_seen: dict = {}
    drain_per_round = max(drain_secs / max(1, rounds), 0.05)
    finished_clean = True

    try:
        for round_idx in range(rounds):
            # Nudge wakes the FC's Subscribe_Rx task. Without this, a freshly
            # powered FC with no traffic in 30+ seconds may not service the
            # USART3 ring promptly (observed 2026-09-13 bench).
            try:
                sock.sendto(b"\x00", (host, port))
            except OSError:
                pass
            sleep(0.1)
            for s in slots:
                stop_req = build_stream_request(
                    [], divider=0, slot=int(s), transport=1)
                try:
                    sock.sendto(stop_req, (host, port))
                except OSError:
                    pass
                print(
                    f"  pre-clear: stop slot {s} "
                    f"(round {round_idx + 1}/{rounds})",
                    file=sys.stderr,
                )
                sleep(0.02)
            sleep(0.3)

            # Drain for this round. Count everything we see; if the FINAL
            # round still shows data frames the FC is not yet clean.
            deadline = time.monotonic() + drain_per_round
            rounds_data_frames = 0
            while time.monotonic() < deadline:
                try:
                    data, _ = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break
                drained += 1
                if len(data) >= 3:
                    frame_types_seen[data[2]] = (
                        frame_types_seen.get(data[2], 0) + 1)
                if DATA_FRAME <= data[2] < DATA_FRAME + 4:
                    rounds_data_frames += 1

            if round_idx == rounds - 1 and rounds_data_frames > 0:
                finished_clean = False
    finally:
        if owns_sock:
            sock.close()

    duration = time.monotonic() - start
    return PreClearResult(
        drained_packets=drained,
        frame_types_seen=frame_types_seen,
        rounds=rounds,
        duration_s=duration,
        finished_clean=finished_clean,
    ).as_dict()


def capture(
    preset_name: str,
    secs: float,
    outdir: Path,
    elf_path: Path,
    wifi_host: str = "192.168.4.1",
    wifi_port: int = 14550,
) -> int:
    pm = MultiSlotPresetManager()
    store = ManifestStore()
    resolver = SymbolResolver(elf_path)

    preset = pm.get(preset_name)
    slots = preset["slots"]
    print(f"# preset {preset_name!r}: {len(slots)} slots", file=sys.stderr)

    # 0. Pre-flight wire-budget check. Compute the aggregate B/s the
    # configured slots would put on USART3, refuse to start if it exceeds
    # the host's safe threshold (80% of the wire -- the firmware's own
    # guard sits at 95%). The arithmetic mirrors what
    # API/subscribe.c::Subscribe_BuildStreamFrame actually emits
    # (12 B overhead + N*size*count value bytes), so a refusal here is the
    # same number the drone's budget guard would also reject.
    # Every slot in a preset is enabled by definition (being in the preset
    # IS the enable), so we map the preset slots to the {enabled, slot,
    # manifest, hz} shape compute_multi_slot_budget() expects.
    enabled_slots = [
        {
            "enabled": True,
            "slot": int(s["slot"]),
            "manifest": s["manifest"],
            "hz": float(s["hz"]),
        }
        for s in slots
    ]
    budget = compute_multi_slot_budget(enabled_slots, store=store)
    print(f"# wire budget: {budget.summary()}", file=sys.stderr)
    for sb in budget.per_slot:
        print(
            f"  slot {sb.slot}: {sb.manifest_name} {sb.hz:.0f} Hz -> "
            f"{sb.bps:.0f} B/s ({sb.wire_pct:.2f}% wire, "
            f"{sb.frame_size_bytes} B/frame)",
            file=sys.stderr,
        )
    if not budget.safe:
        raise RuntimeError(
            f"refusing to start: preset {preset_name!r} would put "
            f"{budget.wire_pct:.1f}% on the USART3 wire "
            f"(threshold 80%). See per-slot breakdown above. Either pick "
            f"a smaller preset, lower per-slot rates, or split the slots "
            f"across two captures."
        )

    outdir.mkdir(parents=True, exist_ok=True)

    # 1. Pre-clear stale FC subscription state. Reusable helper: any host
    # can call this without depending on the dashboard's inline version.
    pre_clear = preclear_fc_subscriptions(
        host=wifi_host,
        port=wifi_port,
    )
    types_str = ", ".join(
        f"0x{ft:02X}×{n}" for ft, n in sorted(pre_clear["frame_types_seen"].items())
    ) or "none"
    print(
        f"# pre-cleared {pre_clear['drained_packets']} stale packets "
        f"({types_str}) in {pre_clear['duration_s']:.2f}s "
        f"{'✓ clean' if pre_clear['finished_clean'] else '⚠ still dirty'}",
        file=sys.stderr,
    )

    # 2. Open ephemeral UDP socket.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)

    # 3. Build subscribe requests, send sequentially with 100 ms gaps.
    rx_buf = bytearray()
    schemas: dict[int, StreamSchema] = {}
    manifests_cfg: list[dict] = []

    for slot_cfg in slots:
        slot = int(slot_cfg["slot"])
        hz = float(slot_cfg["hz"])
        manifest_name = slot_cfg["manifest"]
        divider = int(100 / hz)
        m = store.get(manifest_name)
        ranges = list(resolve_ranges_from_names(m.vars, resolver))
        manifests_cfg.append(
            {
                "slot": slot,
                "manifest": manifest_name,
                "hz": hz,
                "divider": divider,
                "ranges": ranges,
            }
        )

    for idx, mcfg in enumerate(manifests_cfg):
        req = build_stream_request(
            slot=mcfg["slot"],
            divider=mcfg["divider"],
            ranges=mcfg["ranges"],
            transport=1,
        )
        print(
            f"  -> subscribe slot {mcfg['slot']} = {mcfg['manifest']!r} @ "
            f"{mcfg['hz']} Hz ({len(mcfg['ranges'])} vars)",
            file=sys.stderr,
        )
        sock.sendto(req, (wifi_host, wifi_port))

        deadline = time.monotonic() + 5.0
        schema_received = False
        while time.monotonic() < deadline and not schema_received:
            try:
                data, _ = sock.recvfrom(2048)
                rx_buf.extend(data)
            except socket.timeout:
                pass

            while True:
                frame = pop_frame(rx_buf)
                if frame is None:
                    break
                frame_type, byte5, payload = frame
                if frame_type == SCHEMA_FRAME:
                    if len(payload) >= 5:
                        schema_total_bytes = (payload[3] << 8) | payload[4]
                    else:
                        schema_total_bytes = 0
                    schemas[mcfg["slot"]] = StreamSchema(
                        slot=mcfg["slot"],
                        divider=mcfg["divider"],
                        transport=1,
                        total_bytes=schema_total_bytes,
                        ranges=tuple(mcfg["ranges"]),
                    )
                    print(
                        f"  <- schema for slot {mcfg['slot']} "
                        f"({schema_total_bytes} B/sample, {byte5} ranges)",
                        file=sys.stderr,
                    )
                    schema_received = True
                    break
                if frame_type == ERROR_FRAME:
                    msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                    raise RuntimeError(
                        f"firmware rejected slot {mcfg['slot']}: {msg}"
                    )

        if not schema_received:
            raise TimeoutError(
                f"slot {mcfg['slot']} schema not received "
                f"(got {len(schemas)}/{len(manifests_cfg)})"
            )

        if idx < len(manifests_cfg) - 1:
            time.sleep(0.1)

    # 4. Build decoder with the schemas we received.
    schema_list = [schemas[s] for s in sorted(schemas.keys())]
    decoder = MultiStreamDecoder(schemas=schema_list)

    # 5. Open per-slot CSV writers.
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    csv_files: dict[int, object] = {}
    csv_writers: dict[int, csv.DictWriter] = {}
    csv_paths: list[Path] = []
    for slot_num in sorted(schemas.keys()):
        mcfg = next(m for m in manifests_cfg if m["slot"] == slot_num)
        path = outdir / (
            f"slot{slot_num}_{mcfg['manifest']}_{int(mcfg['hz'])}hz_"
            f"{timestamp}.csv"
        )
        fh = open(path, "w", newline="")
        writer = csv.DictWriter(fh, fieldnames=["sample_idx", "tick", "data_hex"])
        writer.writeheader()
        csv_files[slot_num] = fh
        csv_writers[slot_num] = writer
        csv_paths.append(path)

    # 6. Stream data until --secs expires.
    print(f"# recording {len(csv_paths)} slots for {secs:g} s", file=sys.stderr)
    counts: dict[int, int] = {s: 0 for s in schemas.keys()}
    start = time.monotonic()
    end = start + secs

    try:
        while time.monotonic() < end:
            try:
                data, _ = sock.recvfrom(2048)
                rx_buf.extend(data)
            except socket.timeout:
                pass

            while True:
                frame = pop_frame(rx_buf)
                if frame is None:
                    break
                frame_type, seq, payload = frame
                if DATA_FRAME <= frame_type < DATA_FRAME + 4:
                    slot = frame_type - DATA_FRAME
                    if slot in csv_writers:
                        # We don't have a tick for raw slot frames; use seq as
                        # a proxy. Same format as dashboard raw CSVs.
                        tick = 0
                        csv_writers[slot].writerow(
                            {
                                "sample_idx": counts[slot] + 1,
                                "tick": tick,
                                "data_hex": payload.hex(),
                            }
                        )
                        counts[slot] += 1

            # Status every ~1 s
            now = time.monotonic()
            if not hasattr(capture, "_last_status") or now - capture._last_status > 1.0:
                capture._last_status = now
                s = " ".join(f"slot{k}={v}" for k, v in sorted(counts.items()))
                print(f"  ... {now - start:.1f}s elapsed  {s}", file=sys.stderr)

    except KeyboardInterrupt:
        print("\n# interrupted", file=sys.stderr)
    finally:
        for fh in csv_files.values():
            fh.close()
        sock.close()

    elapsed = time.monotonic() - start
    print(f"# done {elapsed:.1f} s", file=sys.stderr)
    for slot_num in sorted(schemas.keys()):
        path = next(p for p in csv_paths if f"slot{slot_num}_" in p.name)
        hz = counts[slot_num] / elapsed if elapsed > 0 else 0
        print(
            f"  slot {slot_num}: {counts[slot_num]} samples -> "
            f"{path.name} (effective {hz:.1f} Hz)",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("preset", help="multi-slot preset name (see multi_slot_presets.yaml)")
    ap.add_argument("--secs", type=float, default=12.0)
    ap.add_argument(
        "--outdir",
        type=Path,
        default=Path("ground_station/logs/multislot"),
    )
    ap.add_argument("--elf", type=Path, default=Path("OBJ/JX_FLY.axf"))
    ap.add_argument("--wifi-host", default="192.168.4.1")
    ap.add_argument("--wifi-port", type=int, default=14550)
    args = ap.parse_args()
    return capture(
        preset_name=args.preset,
        secs=args.secs,
        outdir=args.outdir,
        elf_path=args.elf,
        wifi_host=args.wifi_host,
        wifi_port=args.wifi_port,
    )


if __name__ == "__main__":
    sys.exit(main())
