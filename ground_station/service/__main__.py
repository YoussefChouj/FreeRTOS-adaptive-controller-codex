"""Entry point: python -m ground_station.service

Starts the ground-station service (WiFi bridge + telemetry ingestion + command
gateway) and the dashboard HTTP shell in one process.

Usage:
    python -m ground_station.service
    python -m ground_station.service --port 8081
    python -m ground_station.service --wifi-host 192.168.4.1 --port 9000
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path so `from ground_station import ...` works
# regardless of where the user invokes from.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ground-station service + dashboard shell.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--port", "-p", type=int, default=8081,
        help="Dashboard HTTP port (default: 8081)",
    )
    p.add_argument(
        "--wifi-host", default="192.168.4.1",
        help="MicoAir WiFi module IP (default: 192.168.4.1)",
    )
    p.add_argument(
        "--wifi-port", type=int, default=14550,
        help="MicoAir command UDP port on the WiFi module (default: 14550)",
    )
    p.add_argument(
        "--no-auto-subscribe", action="store_true",
        help="Skip the automatic boot-default subscribe request on start.",
    )
    p.add_argument(
        "--rtos-bridge", action="store_true",
        help="Enable the SWD-backed RTOS observability bridge "
             "(reads platform_obs_send_ticks / queue_depth / dma_busy "
             "/ xTickCount and injects them as streams['rtos']). "
             "Off by default; safe to leave off in CI.",
    )
    p.add_argument(
        "--rtos-interval", type=float, default=5.0,
        help="RTOS bridge polling rate in Hz (default: 5). "
             "Only takes effect when --rtos-bridge is set.",
    )
    p.add_argument(
        "--preset", default=None,
        help="Auto-load a multi-slot preset at startup. Reuses the "
             "proven `livewatch.capture_preset` pipeline: "
             "preclear_fc_subscriptions() -> MultiSlotPresetManager.get() "
             "-> per-slot subscribe_slot(). Default: none. "
             "Available presets live in "
             "ground_station/livewatch/multi_slot_presets.yaml "
             "(flight_comprehensive, mrac_characterization, "
             "thrust_validation).",
    )
    p.add_argument(
        "--freshness-ttl", type=float, default=30.0,
        help="Slot freshness TTL in seconds. Slots whose "
             "last_update_ns is older than this are evicted from "
             "/state to prevent stale-value pollution (default: 30).",
    )
    return p


def apply_startup_preset(service, preset_name: str, *,
                         wifi_host: str = "192.168.4.1",
                         wifi_port: int = 14550) -> None:
    """Reuse the proven CLI capture pipeline to auto-load a multi-slot preset.

    This is the dashboard-side equivalent of `python -m
    ground_station.livewatch.capture_preset flight_comprehensive` -- it
    pre-clears any stale FC subscription state, asks the bridge to
    subscribe each slot in the preset, and lets the normal ingest path
    populate `/state.streams[slot]`.

    The function lives in `service.__main__` (not `core.py`) because it
    needs both the live `GroundStationService` AND the bridge's WiFi
    socket. It is intentionally idempotent: calling it twice on a quiet
    FC just re-issues the subscribes.

    Why reuse `capture_preset`, not reimplement:
      * `preclear_fc_subscriptions` drains 246+ stale packets on cold-boot
        (see the captured 2026-09-13 journal). Without it, the first
        subscribe gets eaten by stale FC state.
      * `MultiSlotPresetManager.get` enforces the REQUIRED_SYNC_VARS
        contract -- a preset that drops the four ARM/FM/vbat/tick vars
        raises ValueError before any wire traffic.
      * `compute_multi_slot_budget` rejects presets that would push the
        USART3 wire past 80 % utilisation. This keeps the dashboard from
        silently killing telemetry.

    Args:
        service: The running GroundStationService (needs bridge set).
        preset_name: e.g. ``"flight_comprehensive"``,
            ``"mrac_characterization"``, ``"thrust_validation"``.
        wifi_host: MicoAir IP. Defaults to factory default.
        wifi_port: MicoAir port. Defaults to factory default.

    Raises:
        RuntimeError: service.bridge is None.
        ValueError: preset name unknown, contract broken, or wire
            budget exceeded.
    """
    if service.bridge is None:
        raise RuntimeError("service.bridge is None -- cannot load preset")

    # The pre-clear below silences subscribe data; stop the bridge watchdog
    # from re-sending the dashboard layout over the preset meanwhile.
    service.bridge._resubscribe_layout = None

    # Lazy import: capture_preset pulls in numpy/pyyaml/etc. We don't want
    # the simple `python -m ground_station.service` startup to pay that
    # cost unless the operator actually asked for a preset.
    from ground_station.livewatch.capture_preset import preclear_fc_subscriptions
    from ground_station.livewatch.manifest import (
        ManifestStore,
        MultiSlotPresetManager,
        compute_multi_slot_budget,
    )

    bridge = service.bridge
    pm = MultiSlotPresetManager()
    preset = pm.get(preset_name)             # raises ValueError if contract broken
    print(f"[service] Preset {preset_name!r}: {len(preset['slots'])} slots, "
          f"loading...", flush=True)

    # 1. Pre-clear stale FC subscription state (3 rounds).
    #    Same routine capture_preset.py uses; drains 246+ packets on
    #    cold-boot. Best-effort: offline FCs raise here and the operator
    #    gets a clear log line.
    try:
        result = preclear_fc_subscriptions(
            host=wifi_host, port=wifi_port,
            slots=range(4), rounds=3, drain_secs=2.0,
        )
        print(f"[service] Pre-clear drained {result['drained_packets']} "
              f"stale packets (clean={result['finished_clean']})", flush=True)
    except Exception as exc:
        print(f"[service] Pre-clear failed (offline FC?): {exc}", flush=True)
        # Don't bail -- the FC may already be clean. Continue.

    # 2. Wire-budget check (rejects presets that would saturate USART3).
    #    Each slot cfg needs an `enabled` flag for the budget calculator;
    #    the YAML presets don't carry one, so default all to enabled.
    budget_cfg = [dict(s, enabled=True) for s in preset["slots"]]
    budget = compute_multi_slot_budget(budget_cfg)
    print(f"[service] Wire budget: {budget.wire_pct:.1f}% of USART3 "
          f"({'safe' if budget.safe else 'OVERBUDGET'})", flush=True)
    if not budget.safe:
        raise RuntimeError(f"preset {preset_name!r} over-budget: "
                           f"{budget.wire_pct:.1f}% USART3")

    # 3. Resolve each slot's manifest to DWARF names, then subscribe.
    store = ManifestStore()
    applied = []
    preset_slots = {}
    for slot_spec in preset["slots"]:
        slot = int(slot_spec["slot"])
        manifest_name = slot_spec["manifest"]
        requested_hz = int(slot_spec["hz"])
        # MIXED-mode rate constant -- matches capture_preset.py:189.
        # At 80 Hz Send_Task, divider = int(80/hz). Rounds 80/50 down to 1.
        divider = max(1, int(80 / requested_hz))
        try:
            mvars = store.get(manifest_name).vars
        except Exception as exc:
            print(f"[service]   slot {slot} manifest "
                  f"{manifest_name!r} failed: {exc}", flush=True)
            continue
        n_ranges = bridge.subscribe_slot(slot=slot, divider=divider, ranges=list(mvars))
        preset_slots[slot] = {"manifest": manifest_name, "n_ranges": n_ranges}
        applied.append((slot, divider, list(mvars)))
        print(f"[service]   slot {slot} -> {manifest_name} "
              f"@ {requested_hz} Hz (divider={divider}, "
              f"{len(mvars)} vars, {n_ranges} ranges)", flush=True)

    def _replay_preset():
        for slot_n, div, names in applied:
            bridge.subscribe_slot(slot=slot_n, divider=div, ranges=names)

    # FC reboot mid-session -> the watchdog restores this preset, not the
    # default dashboard layout.
    bridge._resubscribe_fn = _replay_preset
    bridge._resubscribe_layout = "preset " + preset_name

    # 4. Wait for 0x08 schema replies to register in the bridge.
    #    After the pre-clear phase the MicoAir may route 0x08 replies to
    #    the ephemeral socket used by preclear_fc_subscriptions instead of
    #    the bridge's port 14550, causing schema registration to fail.
    #    This retry loop re-subscribes slots whose schemas did not arrive
    #    within the timeout, up to MAX_RETRIES times.
    MAX_RETRIES = 3
    TIMEOUT_S = 1.5

    def _matches(schema, entry):
        # A late 0x08 for the default layout must not count as the preset's.
        return (schema is not None and schema.divider == entry[1]
                and len(schema.ranges) == preset_slots[entry[0]]["n_ranges"])

    def _missing_slots():
        with bridge._stream_lock:
            return [a for a in applied if not _matches(bridge._stream_schemas.get(a[0]), a)]

    time.sleep(TIMEOUT_S)
    for attempt in range(1, MAX_RETRIES + 1):
        missing = _missing_slots()
        if not missing:
            break
        for slot_n, div, names in missing:
            print(f"[service]   RETRY {attempt}: slot {slot_n} schema missing, "
                  f"re-subscribing ({preset_slots[slot_n]['manifest']})", flush=True)
            bridge.subscribe_slot(slot=slot_n, divider=div, ranges=names)
        time.sleep(TIMEOUT_S)

    missing_ids = {a[0] for a in _missing_slots()}
    for slot_n, _div, _names in applied:
        if slot_n in missing_ids:
            print(f"[service]   slot {slot_n} schema MISSING after "
                  f"{MAX_RETRIES} retries", flush=True)
        else:
            print(f"[service]   slot {slot_n} schema registered "
                  f"({len(bridge._stream_schemas[slot_n].ranges)} ranges)", flush=True)

    print(f"[service] Preset {preset_name!r} loaded. Wait ~2 s for "
          f"0x08 schema replies; /state.streams will populate.", flush=True)


def main() -> None:
    parser = _build_argparser()
    args = parser.parse_args()

    # ---- Build the service stack ----
    from ground_station.comm.wifi_bridge import WifiBridge
    from ground_station.platform.experiments import ExperimentRuntime
    from ground_station.platform.shell import start_shell
    from ground_station.service.core import GroundStationService

    bridge = WifiBridge(
        wifi_host=args.wifi_host,
        wifi_port=args.wifi_port,
        on_telemetry=None,  # set below after service exists
    )

    # Experiment runtime: ticks on every telemetry ingest so active experiments
    # can record samples and advance their settle/measure state machine.
    # Parameters are empty at startup; each POST /experiments defines its own set.
    experiment_runtime = ExperimentRuntime({})

    service = GroundStationService(bridge=bridge,
                                   experiment_runtime=experiment_runtime)
    service.set_slot_freshness_ttl(args.freshness_ttl)

    # Wire bridge → service so decoded frames flow into the HTTP state snapshot.
    # wifi_bridge decodes frames internally; pass (tag, payload, metadata)
    # tuples to ingest_decoded. The typed signature keeps the payload dict
    # free of __stream_metadata__ injection — the service's adapter consumes
    # the StreamMetadata directly via the metadata= keyword.
    # The bridge's rx loop invokes ``_on_telemetry(tag, payload)``; metadata
    # is None so the adapter hoists defaults / legacy slotN.* keys.
    bridge._on_telemetry = (
        lambda tag, payload: service.ingest_decoded(tag, payload)
    )

    # ---- Start session + bridge ----
    service.start(auto_subscribe=not args.no_auto_subscribe)

    # ---- Multi-slot preset auto-load (optional, off by default) ----
    # Reuses the proven CLI pipeline from `capture_preset.py` so the
    # dashboard service populates slots 1..3 the same way the CLI capture
    # tool does. Pre-clears stale FC subscription state, then issues one
    # 0x21 subscribe per slot in the preset.
    if args.preset:
        try:
            apply_startup_preset(service, args.preset,
                                 wifi_host=args.wifi_host,
                                 wifi_port=args.wifi_port)
        except Exception as exc:
            print(f"[service] WARNING: preset {args.preset!r} failed to "
                  f"auto-load: {exc}", flush=True)

    # ---- RTOS bridge (optional, off by default) ----
    # The bridge connects to the wireless CMSIS-DAP probe and reads
    # ``platform_obs_send_ticks``, ``platform_obs_queue_depth``,
    # ``platform_obs_dma_busy``, and ``xTickCount``. It publishes the
    # values via ``service.inject_external_stream(slot='rtos', ...)``
    # so the dashboard ``resource-panel.js`` can render them. Skips
    # silently if no probe is attached (typical for CI runs).
    rtos_bridge = None
    if args.rtos_bridge:
        from ground_station.platform.rtos_bridge import build_bridge
        rtos_bridge = build_bridge(service, interval_hz=args.rtos_interval)
        rtos_bridge.start()

    print(
        f"[service] Ground-station service started\n"
        f"  WiFi:      {args.wifi_host}:{args.wifi_port}\n"
        f"  Dashboard: http://localhost:{args.port}\n"
        f"  RTOS:      {'on @ ' + str(args.rtos_interval) + ' Hz' if args.rtos_bridge else 'off'}\n"
        f"  Preset:    {args.preset if args.preset else 'off (slot 0 only)'}\n"
        f"  Slot TTL:  {args.freshness_ttl:.0f} s\n"
        f"  Session:   {service.session_id}\n"
        f"\n  Press Ctrl+C to stop.\n",
        flush=True,
    )

    # ---- Graceful shutdown ----
    def _stop(signum, _frame):
        print("\n[service] Stopping...", flush=True)
        if rtos_bridge is not None:
            rtos_bridge.stop()
        service.stop()
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    # ---- Terminal manager (optional, PTY-backed) ----
    terminal_mgr = None
    try:
        from ground_station.service.terminal import TerminalManager
        terminal_mgr = TerminalManager()
        print(f"[terminal] token file: {terminal_mgr.get_token_file()}",
              file=sys.stderr)
    except Exception as exc:
        print(f"[service] terminal: {exc}", file=sys.stderr)

    # ---- Start the HTTP shell (blocks) ----
    # Keep main thread alive so daemon HTTPServer thread stays up.
    # Signal handler above handles Ctrl+C and calls sys.exit(0).
    try:
        api = start_shell(service, port=args.port,
                          experiment_runtime=experiment_runtime,
                          terminal_manager=terminal_mgr)
        print(f"[service] Shell bound to http://localhost:{args.port}", flush=True)
        # Block indefinitely — signal handler will exit the process
        # time.sleep keeps the main thread alive so the daemon HTTPServer
        # thread (api.server.serve_forever) keeps running.
        while True:
            time.sleep(86400)
    except Exception as exc:
        print(f"[service] Shell failed: {exc}", flush=True)
        if rtos_bridge is not None:
            rtos_bridge.stop()
        service.stop()
        bridge.stop()
        raise


if __name__ == "__main__":
    main()
