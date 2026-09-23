"""WiFi bridge: commands + telemetry between MicoAir WiFi module and the dashboard.

Architecture
~~~~~~~~~~~~
The MicoAir module (2.4 GHz, 921600 baud) connects USART3 on the FC to a
UDP port on the host. Two separate paths are needed:

COMMAND PATH (dashboard -> FC)
  The WiFi module only forwards inbound bytes to the FC's USART3 RX DMA.
  The firmware's USART3_IRQHandler dispatches them as standard 0xCC 0xDD
  command frames (CMD 0x01..0x18), just as it does over UART5.
  This path is already fully wired: no firmware change needed.

TELEMETRY PATH (FC -> dashboard)
  USART3 TX carries one of three things, in this priority order:
    1. A 0x09-slot subscribe stream (when the FC has accepted a 0x21 request
       targeting USART3 as the transport).
    2. The 16-byte JustFloat attitude frame, when no subscribe stream is
       active -- the usart3_send fallback path.
    3. (Earlier firmware revisions also mirrored 0xAA 0xAA Frame A; that
       path is gone after the 2026-08-09 USART3 arbitration rewrite.)
  The bridge decodes whichever frame arrives and forwards it to the
  dashboard over UDP 1350. For subscribe-stream frames it ALSO forwards
  the values as JustFloat to UDP 1347 / 1348 for VoFA+ live plotting.

Subscribe control plane
~~~~~~~~~~~~~~~~~~~~~~~
The 0xCC 0xDE 0x21 subscribe request is accepted on USART3 (WiFi) and
optionally on UART5 (CMSIS-DAP) when SUBSCRIBE_UART5_ENABLED=1. The default
is USART3-only (production). The 0x08 schema reply and 0x09..0x0C data frames
are sent back on the same transport the request arrived on.

This bridge can both REQUEST subscriptions (via `--manifest` mode) and DECODE
the resulting streams. When invoked with `--manifest`, it sends the 0x21
requests, waits for 0x08 schema replies, then logs the 0x09+slot data frames
to CSV.

Wire protocol
~~~~~~~~~~~~~
  Bridge UDP command port <- dashboard sends JSON:
    {"cmd_id": int, "index": int, "value": float}
    -> bridge builds 0xCC 0xDD [CMD][INDEX][VALUE float32 LE][XOR-CRC8]
    -> sends to FC over WiFi (192.168.4.1:14550)

  Bridge WiFi RX <- FC sends raw USART3 bytes:
    Decodes Frame A (legacy 0xAA 0xAA 0x01) -- if present in this build
    Decodes Frame B / ID / C (0xAA 0xBB TYPE) when the mirror is up
    Decodes subscribe data frames (0xAA 0xBB 0x09..0x0C)
    Decodes the 16-byte JustFloat fallback
    -> JSON to dashboard UDP 1350
    -> JustFloat to VoFA+ UDP 1347 (Frame A, slot 1) and 1348 (Frame B, slot 2)

Usage
~~~~~
  python -m ground_station.comm.wifi_bridge
  python -m ground_station.comm.wifi_bridge --wifi-host 192.168.4.1 --wifi-port 14550 \\
      --cmd-port 1349 --telem-port 1350 \\
      --vofa-host 127.0.0.1 --vofa-port-a 1347 --vofa-port-b 1348
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .boot_default_layout import (
    BOOT_DEFAULT_VARS,
    BOOT_DEFAULT_DIVIDER,
    DASHBOARD_FRAME_A_VARS,
    DASHBOARD_FRAME_A_DIVIDER,
    DASHBOARD_PANEL_EXTRA_VARS,
    DASHBOARD_PANEL_EXTRA_DIVIDER,
)
# Subscribe stream constants reused for the bytes/s budget projection in
# /subscribe/preview. FRAME_OVERHEAD mirrors SUBSCRIBE_STREAM_FRAME_OVERHEAD
# in API/subscribe.h; the payload is the sum of resolved DWARF symbol sizes.
from ground_station.livewatch.stream import FRAME_OVERHEAD  # noqa: E402

from ground_station.platform.transactions import Command as TransactionCommand
from ground_station.platform.transactions import build_command as build_transaction_command
from ground_station.platform.transactions import Result as TransactionResult
from ground_station.platform.transactions import parse_result_parts


def _parse_simple_yaml(path: Path) -> Dict[str, Any]:
    """Load a config.yaml without a full YAML parser dependency."""
    result: Dict[str, Any] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("[") and val.endswith("]"):
            result[key] = [s.strip() for s in val[1:-1].split(",")]
        else:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    if val in ("true", "false"):
                        result[key] = val == "true"
                    else:
                        result[key] = val
    return result


def load_config() -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "wifi_bridge_host": "192.168.4.1",
        "wifi_bridge_wifi_port": 14550,
        # 1349 is the cmd port the dashboard pings by default (matches serial_bridge.py).
        # The legacy 14551 default broke auto-connect; keep it readable in the help text
        # but do not default to it.
        "wifi_bridge_cmd_port": 1349,
        "wifi_bridge_telem_port": 1350,
        # Preset-subscription mode. 'burst' (default) aggregates multiple
        # preset variable lists into ONE slot's 0x21 request when the
        # combined payload fits STREAM_MAX_BYTES (1024 B), so a single
        # Send_Task cycle emits one wider data frame instead of N narrow
        # ones. 'single' is the legacy per-preset-per-slot behaviour.
        # Telemetry-heavy sessions (limit-test, MRAC tuning, multi-axis
        # flight-test) benefit from burst; one-preset ad-hoc subscriptions
        # are equivalent under either mode.
        "wifi_bridge_mode": "burst",
    }
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    overrides = _parse_simple_yaml(config_path)
    defaults.update(overrides)
    return defaults

try:
    import serial
except ImportError:
    serial = None


# Frame layouts (mirrors TASK/send_data.c / SerialBridge frame unpacking)
_FRAME_A_LEN = 68   # 0xAA 0xAA [0..11] [0..11] ... [acc 0..2] [gyro 0..2] [mag] [baro] [bat]
                     # Frame A fields after [0..11]:
                     #   4 × PID output (pitch/roll/yaw/z) + 4 × motor output
_FRAME_A_PAYLOAD = 12 + 4 + 4 + 4 + 3 + 3 + 1 + 1 + 2  # ≈ 34 bytes of interest
_FRAME_A_TAIL = b"\x00\x00\x80\x7f"


def _rpm_scalar_keys(rpm_list: list) -> dict:
    return {f"motor.rpm_{i}": int(rpm_list[i]) for i in range(len(rpm_list))}


class WifiBridge:
    """Bidirectional bridge between MicoAir WiFi and the dashboard."""

    def __init__(
        self,
        wifi_host: str = "192.168.4.1",
        wifi_port: int = 14550,
        # 1349 = dashboard's auto-connect cmd port (matches serial_bridge.py).
        # The legacy 14551 default broke auto-detect; the loader default now wins.
        cmd_udp_port: int = 1349,
        telem_udp_port: int = 1350,
        # For the nudge: MicoAir listens on this UDP port for the 1-byte nudge
        # that tells it to aim its downlink at the sender's source port.
        nudge_port: int = 14550,
        nudge_host: Optional[str] = None,
        # VoFA+ forwarding (JustFloat LE float32 + 00 00 80 7F tail).
        # The 1347 / 1348 defaults mirror SerialBridge's. vofa_enabled=False
        # leaves the path wired but silent (useful for tests).
        vofa_enabled: bool = True,
        vofa_host: str = "127.0.0.1",
        vofa_port_a: int = 1347,
        vofa_port_b: int = 1348,
        # Preset-subscription mode. 'burst' (default for telemetry-heavy
        # sessions) routes subscribe_preset() through
        # PresetManager.build_burst_request() so multiple presets aggregate
        # into one slot when they fit. 'single' is the legacy
        # build_request() path. The CLI flag --mode and the wifi_bridge_mode
        # key in config.yaml feed this argument.
        mode: str = "burst",
        # Optional callback: called with (tag: str, payload: dict) for every
        # decoded telemetry frame. Use this to feed GroundStationService.ingest_decoded().
        # The callback receives (tag, payload) tuples from _rx_loop after the bridge
        # has decoded the frame internally using its own schemas.
        on_telemetry=None,
        # The MicoAir module routes USART3 downlink to the source of the MOST
        # RECENT uplink datagram. The 1-byte nudge sent at start() aims it at
        # this host, but the module's route (session) mapping idles out after
        # ~8-10 min of silence, silently stopping streaming. A periodic
        # re-nudge keeps the route fresh. 1-byte 0x00 is a no-op command the
        # FC ignores (see docs/skills/micoair-connect.md). 30 s is far inside
        # the module idle window but cheap (1 B / 5 s).
        keepalive_interval: float = 5.0,
    ):
        self._wifi_host = wifi_host
        self._wifi_port = wifi_port
        self._cmd_udp_port = cmd_udp_port
        self._telem_udp_port = telem_udp_port
        self._nudge_port = nudge_port
        self._nudge_host = nudge_host or wifi_host
        self._vofa_enabled = vofa_enabled
        self._vofa_host = vofa_host
        self._vofa_port_a = vofa_port_a
        self._vofa_port_b = vofa_port_b
        self._on_telemetry = on_telemetry  # optional (tag, payload) callback for service integration

        self._stop = threading.Event()
        # Subscribe watchdog. The FC keeps its slots in RAM, so a power cycle
        # drops them: it comes back sending only its fallback frames, and every
        # subscribed key froze at its last value while the link looked alive.
        # When the bridge auto-subscribed at start, it re-sends that request
        # whenever other frames arrive but no subscribe data has for a while.
        self._resubscribe_layout: Optional[str] = None
        self._last_stream_rx = 0.0
        self._last_resubscribe = 0.0
        self._cmd_queue: queue.Queue[Optional[Dict[str, Any]]] = queue.Queue()
        # Keep-alive state: monotonic time of the last downlink-aim nudge. We
        # re-nudge every `keepalive_interval` s to stop the MicoAir module's
        # route expiring (~8-10 min idle), which would otherwise silently halt
        # all USART3 telemetry with no socket error and no firmware change.
        self._keepalive_interval = keepalive_interval
        self._last_nudge = 0.0

        # Subscribe-stream schemas, one per slot. Populated by
        # `apply_manifest_schema()` (called by the launcher when a layout
        # is in effect). Each schema is the StreamSchema returned by
        # `livewatch.stream.subscribe()`; we keep the per-slot schema
        # so we can name channels for VoFA+ and the dashboard.
        # slot index 1..3 -- slot 0 is reserved for livewatch's own
        # auto-subscribe (4-place collision noted in the 2026-08-09
        # pipeline-hardening session).
        self._stream_schemas: Dict[int, Any] = {}
        self._stream_lock = threading.Lock()
        # Per-slot sequence accounting is kept at the transport boundary so
        # dashboards can distinguish radio loss from a stale value. The
        # firmware sequence is modulo-256 and is independent for each slot.
        self._stream_stats: Dict[int, Dict[str, int]] = {}

        # Pending range names per slot. The wire 0x08 reply only echoes
        # address/size/count — NOT the DWARF names — so the host has to
        # remember the names it sent in the 0x21 request and re-attach
        # them when the schema reply arrives. Without this, every
        # dashboard panel falls back to ``chN.M`` positional channels
        # because ``rng.name`` is empty. See S15-audit.md §1 for the
        # live-verification trace (22 DWARF symbols requested, only 3
        # names resolved before this fix).
        #
        # Keyed by slot; value is the requested ``StreamRange`` tuple
        # ordered exactly as the wire 0x21 sent them. Consumed by
        # ``_handle_schema_frame``.
        self._pending_schema_ranges: Dict[int, tuple] = {}

        # Telemetry state (written by RX thread, read by UDP send thread)
        self._last_telem: Dict[str, Any] = {}
        self._last_telem_t: float = 0.0
        self._telem_lock = threading.Lock()

        # Socket used for both WiFi TX and RX
        self._wifi: Optional[socket.socket] = None
        self._wifi_send: Optional[socket.socket] = None
        self._cmd_udp: Optional[socket.socket] = None
        self._telem_udp: Optional[socket.socket] = None
        self._udp_send: Optional[socket.socket] = None   # separate socket for mirror
        self._transaction_id = 0
        self._transaction_results: queue.Queue[TransactionResult] = queue.Queue()

        # Preset-subscribe state (written/read by calling thread and RX thread)
        self._preset_schema_event = threading.Event()
        self._pending_preset_slot: Optional[int] = None
        self._pending_preset_id: Optional[int] = None
        # Multi-slot variant for burst mode. When non-empty, the RX thread
        # removes the schema's slot from this set on every accepted 0x08,
        # and sets the event when the set drains to empty. Single mode
        # leaves it empty and falls back to the legacy _pending_preset_slot
        # match (kept for backward compatibility with callers that race
        # burst/single in the same process).
        self._pending_preset_slots: set[int] = set()

        # Preset-subscription mode. 'burst' (default) tries to aggregate
        # multiple presets into one slot via PresetManager.build_burst_request();
        # 'single' is the legacy one-preset-per-slot path. The CLI flag
        # --mode and config.yaml's wifi_bridge_mode both feed this attribute;
        # subscribe_preset() also accepts an explicit `mode` override.
        self._mode: str = "burst"

        # SymbolResolver for preset variable resolution (built lazily in start())
        self._preset_resolver: Optional["SymbolResolver"] = None

        # Per-slot subscribe lifecycle state. Values follow the transaction
        # state machine: "planned" (armed to send) -> "sent" (0x21 request on
        # the wire) -> "schema_received" (0x08 ack decoded) -> "streaming"
        # (0x09+slot data frames flowing). "error" holds a machine-readable
        # reason. The service exposes these to the dashboard so the Slot
        # Manager can render pending / acked / live / error per slot.
        # Keyed by slot 0..3 (SUBSCRIBE_MAX_SLOTS = 4).
        self._slot_states: Dict[int, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        auto_subscribe_boot_default: bool = True,
        initial_telemetry_mode: Optional[str] = None,
    ) -> None:
        """Start all threads and return.

        Raises OSError if a port is already in use. The caller (main()) is
        responsible for catching that and printing a user-friendly message.
        Without this, an orphan bridge from a previous session silently kills
        the new one and the dashboard's ping times out for no visible reason.

        Args:
            auto_subscribe_boot_default: If True, sends a 0x21 subscribe request
                for slot 0 using the dashboard layout (the legacy Frame A
                payload, now routed over the subscribe path) and captures the
                0x08 schema. This is what makes the dashboard sidebar
                (``status.*`` and ``mrac.*`` keys) populate over WiFi. The
                firmware's 12-var boot-default is still available via
                ``_request_slot0_schema(layout="boot_default")`` for callers
                that need it. Default: True.
            initial_telemetry_mode: If set, sends a CMD 0x0F telemetry-mode
                switch on the WiFi socket BEFORE the auto-subscribe fires, so
                the FC's ``g_telemetry_mode`` is settled before the first
                data frame arrives. One of ``"legacy"`` (idx 100),
                ``"mixed"`` (idx 101, boot default), or ``"subscribe_only"``
                (idx 102). Value is ignored. See
                ``set_telemetry_mode_now()`` for the SUBSCRIBE_ONLY
                control-plane caveat. Default: None (no mode switch sent —
                FC keeps whatever mode it booted into).
        """
        self._wifi = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._wifi.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind to port 14550: this socket handles BOTH nudge send (to aim MicoAir
        # downlink) AND all response/telemetry frames from the MicoAir (which routes
        # responses back to the send port). Previously this was an ephemeral port,
        # which received telemetry but NOT command result frames (those arrive from
        # port 14550 where _wifi_send was bound). Merging into one socket fixes
        # the routing: all inbound data goes to the same socket that sent the nudge.
        self._wifi.bind(("0.0.0.0", 14550))
        self._wifi.settimeout(0.1)

        # Nudge on the same socket so MicoAir routes the response here.
        try:
            self._wifi.sendto(b"\x00", (self._nudge_host, self._nudge_port))
        except OSError:
            pass

        # _wifi_send is the same socket as _wifi (merged for unified RX/TX on port 14550).
        # Point the attribute at _wifi so send_transaction / _send_cmd_frame work unchanged.
        self._wifi_send = self._wifi

        self._cmd_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cmd_udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._cmd_udp.bind(("0.0.0.0", self._cmd_udp_port))
        self._cmd_udp.settimeout(0.2)

        self._telem_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._telem_udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._telem_udp.setblocking(False)

        self._udp_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._transaction_id = 0
        self._transaction_results = queue.Queue()

        # Telemetry-mode switch BEFORE threads start so the FC's
        # g_telemetry_mode is settled on the first Send_Task cycle. We use
        # the wifi socket directly (not the cmd queue) so this fires once
        # per start() and is observable in the FC's USART3 IRQ handler
        # before the auto-subscribe request that follows.
        if initial_telemetry_mode is not None:
            self.set_telemetry_mode_now(initial_telemetry_mode)

        rx = threading.Thread(target=self._rx_loop, name="wifi_bridge_rx", daemon=True)
        cmd = threading.Thread(target=self._cmd_loop, name="wifi_bridge_cmd", daemon=True)
        telem = threading.Thread(target=self._telem_loop, name="wifi_bridge_telem", daemon=True)

        rx.start()
        cmd.start()
        telem.start()

        self._threads = (rx, cmd, telem)

        # Auto-subscribe to the dashboard layout (slot 0). This drives the
        # dashboard's sidebar via _slot0_to_sidebar() — see boot_default_layout
        # docstring for the dashboard vs firmware-mirror distinction.
        if auto_subscribe_boot_default:
            self._resubscribe_layout = "dashboard"
            self._last_resubscribe = time.monotonic()
            self._request_slot0_schema(layout="dashboard")

    # Subscribe data silent this long while other frames arrive = FC rebooted.
    _RESUBSCRIBE_AFTER_S = 3.0
    # Minimum spacing between re-sends, so a rejected request does not spam.
    _RESUBSCRIBE_EVERY_S = 10.0

    def _check_resubscribe(self) -> None:
        """Re-send the auto-subscribe when the FC has evidently forgotten it.

        Called on the RX thread for every non-subscribe frame. The request
        resolves DWARF symbols, so it runs on its own thread.
        """
        layout = self._resubscribe_layout
        if layout is None:
            return
        now = time.monotonic()
        if now - self._last_stream_rx < self._RESUBSCRIBE_AFTER_S:
            return
        if now - self._last_resubscribe < self._RESUBSCRIBE_EVERY_S:
            return
        self._last_resubscribe = now
        print("[wifi_bridge] frames arrive but no subscribe data: FC likely "
              f"rebooted, re-sending the {layout} subscribe",
              file=sys.stderr, flush=True)
        threading.Thread(target=self._request_slot0_schema, args=(layout,),
                         name="wifi_bridge_resubscribe", daemon=True).start()

    def stop(self) -> None:
        """Stop all threads."""
        self._stop.set()
        for t in getattr(self, "_threads", ()):
            t.join(timeout=1.0)
        for s in (self._wifi, self._wifi_send, self._cmd_udp, self._telem_udp, self._udp_send):
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    def send_command(self, cmd_id: int, index: int, value: float) -> None:
        """Enqueue a command (thread-safe, for programmatic use)."""
        self._cmd_queue.put_nowait({"cmd_id": cmd_id, "index": index, "value": value})

    def send_transaction(self, cmd_id: int, index: int, value: float,
                         flags: int = 0) -> int:
        """Send a versioned command envelope and return its transaction ID."""
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        if self._transaction_id == 0:
            self._transaction_id = 1
        command = TransactionCommand(self._transaction_id, cmd_id, index, value, flags)
        self._send_transaction_frame(build_transaction_command(command))
        return command.transaction_id

    def poll_transaction_result(self, timeout: float = 0.0) -> Optional[TransactionResult]:
        """Return the next correlated transaction outcome, if available."""
        try:
            return self._transaction_results.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def apply_manifest_schema(self, slot: int, schema) -> None:
        """Record a per-slot schema so 0x09+slot frames can be named.

        The launcher calls this once a manifest layout has been applied over
        the wireless-debugger UART5 sink. Without a schema, a 0x09+slot
        frame is decoded as raw float32s under sequential channel numbers.
        """
        with self._stream_lock:
            self._stream_schemas[slot] = schema
            self._stream_stats[slot] = {"received": 0, "dropped": 0,
                                        "crc_errors": 0, "last_seq": -1}

    def _request_slot0_schema(self, layout: str = "dashboard") -> None:
        """Send 0x21 subscribe request for slot 0 using the chosen layout.

        Fires asynchronously after ``start()``. The 0x08 schema reply will
        arrive on the RX thread and be decoded by ``_parse_one`` →
        ``_handle_schema_frame``.

        Args:
            layout: ``"dashboard"`` (default) drives the dashboard sidebar
                via ``_slot0_to_sidebar`` — 22 vars (Euler + MRAC bars +
                status flags + vbat) at divider 4 → ~20 Hz wire rate in
                MIXED mode (MIXED cadence is ~80 Hz, not the nominal 200).
                ``"boot_default"`` mirrors the firmware's 12-var boot
                default at divider 20 → ~10 Hz wire rate.

        See ``ground_station/comm/boot_default_layout.py`` for the var lists
        and divider rationale. With ``layout="dashboard"`` a second request
        is fired for slot 1 carrying ``DASHBOARD_PANEL_EXTRA_VARS`` — the
        Frame B/C-equivalent variables the block-diagram / estimator /
        safety panels read (2026-09-22 binding-table task).
        """
        if layout == "dashboard":
            vars_tuple = DASHBOARD_FRAME_A_VARS
            divider = DASHBOARD_FRAME_A_DIVIDER
            label = "dashboard"
        elif layout == "boot_default":
            vars_tuple = BOOT_DEFAULT_VARS
            divider = BOOT_DEFAULT_DIVIDER
            label = "boot-default"
        else:
            raise ValueError(
                f"layout must be 'dashboard' or 'boot_default', got {layout!r}"
            )

        self._request_stream_schema(0, vars_tuple, divider, label)
        if layout == "dashboard":
            # Panel extras ride on slot 1: the firmware caps a slot at
            # SUBSCRIBE_MAX_STREAM_RANGES = 62 ranges (API/subscribe.h:189),
            # and 54 sidebar + 25 panel vars on one slot would be rejected
            # whole with "E:too many ranges", killing the sidebar stream.
            self._request_stream_schema(
                1, DASHBOARD_PANEL_EXTRA_VARS, DASHBOARD_PANEL_EXTRA_DIVIDER,
                "dashboard-panel-extras")

    def _request_stream_schema(self, slot: int, vars_tuple, divider: int,
                               label: str) -> None:
        """Send a 0x21 subscribe request for one slot's var tuple.

        Shared by the slot-0 dashboard/boot-default layouts and the slot-1
        panel-extras layout (2026-09-22 binding-table task). The 0x08
        schema reply arrives on the RX thread and is decoded by
        ``_parse_one`` → ``_handle_schema_frame``, which reads the slot's
        entry in ``_pending_schema_ranges`` to name the channels.
        """
        try:
            from ground_station.livewatch.stream import build_stream_request, StreamRange
            from ground_station.livewatch.symbols import SymbolResolver

            # Resolve DWARF addresses for the chosen slot-0 variables
            elf_path = Path(__file__).parents[2] / "OBJ" / "JX_FLY.axf"
            if not elf_path.exists():
                print(f"[wifi_bridge] Warning: {elf_path} not found, skipping {label} schema request",
                      file=sys.stderr, flush=True)
                return

            resolver = SymbolResolver(str(elf_path))
            self._preset_resolver = resolver
            ranges = []
            for var_name in vars_tuple:
                try:
                    symbol = resolver.resolve(var_name)
                    ranges.append(StreamRange(
                        address=symbol.address,
                        size=symbol.size,
                        count=1,
                        name=var_name,
                        fmt=symbol.fmt
                    ))
                except Exception as e:
                    print(f"[wifi_bridge] Warning: failed to resolve {var_name}: {e}",
                          file=sys.stderr, flush=True)
                    continue

            if not ranges:
                print(f"[wifi_bridge] Warning: no variables resolved, skipping {label} schema request",
                      file=sys.stderr, flush=True)
                return

            # Build 0x21 request. TRANSPORT_USART3 = 1.
            request = build_stream_request(
                ranges=ranges,
                divider=divider,
                transport=1,
                usart3_baud=921600,
                slot=slot,
                other_bps=0
            )

            # Save the requested ranges keyed by slot. The wire 0x08
            # reply only echoes address/size/count — NOT the DWARF names
            # — so we need to remember which name went with which
            # address to populate ``schema.ranges[i].name`` later.
            # Without this the decoder falls back to ``chN.M`` for
            # every variable. See S15-audit.md §1.
            with self._stream_lock:
                self._pending_schema_ranges[slot] = tuple(ranges)

            # S15 instrumentation: log the 0x21 request bytes so we can
            # verify what was sent on the wire when a schema reply comes
            # back with the wrong (or no) names.
            preview = request.hex()
            print(
                f"[wifi_bridge] [S15] Sent {label} schema request "
                f"(slot {slot}, {len(ranges)} vars, divider={divider}) "
                f"bytes={preview[:64]}...",
                flush=True,
            )
            print(
                f"[wifi_bridge] [S15] Requested ranges:\n" + "\n".join(
                    f"    0x{r.address:08X} size={r.size} count={r.count} "
                    f"name={r.name!r}" for r in ranges
                ),
                flush=True,
            )

            # Send over WiFi
            self._wifi_send.sendto(request, (self._wifi_host, self._wifi_port))

        except Exception as e:
            print(f"[wifi_bridge] Warning: {label} schema request failed: {e}",
                  file=sys.stderr, flush=True)

    # Backwards-compat alias for callers that still pass no layout. Kept as a
    # thin wrapper so the legacy name resolves to the new dashboard default.
    def _request_boot_default_schema(self) -> None:
        """Deprecated: prefer ``_request_slot0_schema(layout=...)``.

        Defaults to the dashboard layout (slot 0 at ~20 Hz wire rate) since
        that is what the dashboard's sidebar consumes. Callers that need the
        firmware-mirror 12-var layout should call
        ``_request_slot0_schema(layout='boot_default')`` explicitly.
        """
        self._request_slot0_schema(layout="dashboard")

    def subscribe_preset(
        self,
        preset_ids: int | list[int],
        slot: int = 1,
        divider: Optional[int] = None,
        transport: int = 1,
        schema_timeout: float = 5.0,
        mode: Optional[str] = None,
    ) -> dict:
        """Subscribe to one or more presets merged into a single slot.

        Merges variable lists on the host, resolves them to DWARF addresses,
        and sends a standard dynamic-range 0x21 subscribe request.
        No firmware changes required. The 0x08 schema names all merged vars.

        Two modes are supported:

          * ``"burst"`` (default; set by the CLI ``--mode`` flag and by
            ``self._mode`` at bridge construction). When ``preset_ids`` has
            more than one entry, the call goes through
            ``PresetManager.build_burst_request()`` which aggregates the
            combined variable list into ONE 0x21 request when the payload
            fits ``STREAM_MAX_BYTES`` (1024 B), or falls back to per-preset
            multi-slot allocation when it does not. The return value's
            ``strategy`` field reports which path was taken. Burst is the
            right default for telemetry-heavy sessions: a single Send_Task
            cycle emits one wider data frame instead of N narrow ones,
            which halves per-cycle header overhead and uses the new
            ``SUBSCRIBE_MAX_FRAMES_PER_TICK`` quota.

          * ``"single"`` (legacy). The call goes through
            ``PresetManager.build_request()`` which always merges the
            variable list into one slot's subscription, regardless of how
            many presets were passed in. Equivalent to burst when only one
            preset is passed.

        Usage::

            from ground_station.comm.subscribe_presets import PresetManager

            bridge.start()

            # Single preset at 50 Hz (burst and single are equivalent)
            info = bridge.subscribe_preset(PresetManager.PRESET_IMU_PID, slot=1)
            print(f"Streaming {info['name']} at {info['hz']} Hz "
                  f"({info['n_ranges']} ranges, {info['bps']} B/s, "
                  f"{info['budget_pct']}% of WiFi budget)")

            # Two presets, burst mode (one slot if it fits, multi-slot fallback)
            info = bridge.subscribe_preset(
                [PresetManager.PRESET_BOOT_DEFAULT,
                 PresetManager.PRESET_IMU_PID],
                slot=1, divider=20, mode="burst"
            )

            # Force legacy single-slot behaviour
            info = bridge.subscribe_preset(
                [PresetManager.PRESET_BOOT_DEFAULT,
                 PresetManager.PRESET_IMU_PID],
                slot=1, divider=20, mode="single"
            )

        Args:
            preset_ids:  Single preset ID (int) or list of preset IDs to merge.
                        Use constants from ``ground_station.comm.subscribe_presets``:
                        ``PRESET_BOOT_DEFAULT`` (0x01), ``PRESET_IMU_PID`` (0x02),
                        ``PRESET_MRAC_FULL`` (0x03).
            slot:        Subscribe slot index (0-3). Default 1.
                        In burst mode this is ``base_slot``: the slot the
                        merged request targets (strategy='burst') or the
                        first slot of the per-preset allocation
                        (strategy='multi-slot').
            divider:     Rate divider. Default depends on mode:
                        single -> first preset's recommended value;
                        burst -> slowest preset's recommended value.
            transport:   1 = USART3/WiFi (default), 0 = UART5/wired.
            schema_timeout: Seconds to wait for the 0x08 schema reply(ies).
                          In burst/multi-slot, the call waits until ALL
                          expected slots have responded, or the timeout
                          fires once for the whole group.
            mode:        ``"burst"`` or ``"single"``. ``None`` uses the
                        bridge's ``self._mode`` attribute (set by the
                        CLI ``--mode`` flag, defaults to ``"burst"``).

        Returns:
            Dict with keys: ids, names, strategy, divider, hz,
            n_ranges, total_bytes, bps, budget_pct, available_bps,
            schemas (burst) or schema (single, legacy alias).
            In burst mode, ``schemas`` is a ``{slot: StreamSchema}`` dict;
            in single mode, ``schema`` is the single registered StreamSchema
            (and ``schemas`` is ``{slot: schema}`` for forward compatibility).
            Both are ``None`` if the schema reply timed out.

        Raises:
            ValueError: Empty list, unknown preset, variable won't resolve,
                        exceeds range/payload/budget limits.
            RuntimeError: Bridge is not running (call ``start()`` first).
        """
        if mode is None:
            mode = self._mode
        if mode not in ("burst", "single"):
            raise ValueError(
                f"mode must be 'burst' or 'single', got {mode!r}"
            )
        if self._wifi is None:
            raise RuntimeError("Bridge is not running — call start() first")

        # Normalise to list
        if isinstance(preset_ids, int):
            preset_ids_list = [preset_ids]
        else:
            preset_ids_list = list(preset_ids)
        if not preset_ids_list:
            raise ValueError("preset_ids cannot be empty")

        # Lazy resolver: if _request_slot0_schema ran, we have it;
        # if not (e.g. auto_subscribe=False), build it now.
        self._ensure_preset_resolver()

        from ground_station.comm.subscribe_presets import PresetManager
        if mode == "burst":
            return self._subscribe_presets_burst(
                preset_ids_list, slot, divider, transport, schema_timeout,
            )
        return self._subscribe_preset_single(
            preset_ids_list, slot, divider, transport, schema_timeout,
        )

    def _ensure_preset_resolver(self) -> None:
        """Build a SymbolResolver from the firmware ELF if we do not have one."""
        if self._preset_resolver is not None:
            return
        from ground_station.livewatch.symbols import SymbolResolver
        elf_path = Path(__file__).parents[2] / "OBJ" / "JX_FLY.axf"
        if elf_path.exists():
            self._preset_resolver = SymbolResolver(str(elf_path))

    def _subscribe_preset_single(
        self,
        preset_ids_list: list,
        slot: int,
        divider: Optional[int],
        transport: int,
        schema_timeout: float,
    ) -> dict:
        """Legacy single-slot path. One 0x21 request, one 0x08 reply."""
        from ground_station.comm.subscribe_presets import PresetManager
        pm = PresetManager(self._preset_resolver)

        request, result = pm.build_request(
            preset_ids_list,
            slot=slot,
            divider=divider,
            transport=transport,
        )

        self._wifi_send.sendto(request, (self._wifi_host, self._wifi_port))

        # Format preset description
        if len(result.preset_ids) == 1:
            name_str = f"{result.preset_names[0]}"
        else:
            name_str = "+".join(result.preset_names)

        print(
            f"[wifi_bridge] Sent preset subscribe (single): "
            f"{name_str}, slot={slot}, divider={result.divider}, "
            f"{result.hz} Hz, {result.n_ranges} ranges, "
            f"{result.total_bytes} B, {result.bps} B/s "
            f"({result.budget_pct}% of WiFi budget)",
            flush=True
        )

        # Wait for 0x08 schema reply on the RX thread.
        # Single-mode wakeup uses _pending_preset_slot (legacy path); the
        # multi-slot `_pending_preset_slots` set is left empty.
        self._preset_schema_event.clear()
        self._pending_preset_slot = slot
        self._pending_preset_id = result.preset_ids[0] if result.preset_ids else None
        self._pending_preset_slots = set()

        arrived = self._preset_schema_event.wait(timeout=schema_timeout)
        self._pending_preset_slot = None
        self._pending_preset_id = None

        # Collect the schema (populated by _handle_schema_frame in the RX thread)
        with self._stream_lock:
            schema = self._stream_schemas.get(slot)

        if not arrived:
            print(
                f"[wifi_bridge] Warning: preset {name_str} (slot {slot}) "
                f"schema reply timed out after {schema_timeout}s — "
                f"0x09+slot frames will be decoded as raw float32 channels",
                file=sys.stderr, flush=True
            )

        return {
            "ids":         result.preset_ids,
            "names":       result.preset_names,
            "strategy":    "single",
            "divider":     result.divider,
            "hz":          result.hz,
            "n_ranges":    result.n_ranges,
            "total_bytes": result.total_bytes,
            "bps":         result.bps,
            "budget_pct":  result.budget_pct,
            "available_bps": result.available_bps,
            "schema":      schema,
            "schemas":     {slot: schema} if schema is not None else {},
        }

    def _subscribe_presets_burst(
        self,
        preset_ids_list: list,
        base_slot: int,
        divider: Optional[int],
        transport: int,
        schema_timeout: float,
    ) -> dict:
        """Burst-mode path: aggregate presets into one slot if they fit,
        otherwise fall back to per-preset multi-slot allocation.

        Uses ``PresetManager.build_burst_request()`` which returns a
        ``BurstRequestResult`` with either one ``(slot, bytes)`` request
        (strategy='burst') or N requests (strategy='multi-slot'). The
        schema wakeup is driven by ``_pending_preset_slots``: the RX
        thread removes each accepted slot from the set and sets the
        schema event when the set drains to empty.
        """
        from ground_station.comm.subscribe_presets import PresetManager
        pm = PresetManager(self._preset_resolver)

        burst_result = pm.build_burst_request(
            preset_ids_list,
            base_slot=base_slot,
            divider=divider,
            transport=transport,
        )

        # Send each request over WiFi. In 'burst' strategy this is one
        # 0x21; in 'multi-slot' this is len(preset_ids_list) 0x21s, each
        # targeting a different slot starting at base_slot.
        for req_slot, request in burst_result.requests:
            self._wifi_send.sendto(request, (self._wifi_host, self._wifi_port))

        # Aggregate metadata for the print + return dict. In 'burst'
        # strategy there is exactly one PresetMergeResult; in 'multi-slot'
        # there is one per preset. The user-visible Hz/BPS are the sum.
        total_ranges = sum(r.n_ranges for r in burst_result.results)
        total_bytes = sum(r.total_bytes for r in burst_result.results)
        total_bps = sum(r.bps for r in burst_result.results)
        peak_budget_pct = max(
            (r.budget_pct for r in burst_result.results), default=0.0,
        )
        # Use the slowest preset's divider as the reported cadence -- it is
        # the binding constraint and matches what burst picks by default.
        reporting_divider = max(
            (r.divider for r in burst_result.results), default=0,
        )

        # Format names. Burst-mode names are joined with '+'; we report
        # them all in the print line for visibility.
        name_str = "+".join(
            n for r in burst_result.results for n in r.preset_names
        )

        print(
            f"[wifi_bridge] Sent preset subscribe ({burst_result.strategy}): "
            f"{name_str}, {len(burst_result.requests)} request(s), "
            f"divider={reporting_divider}, "
            f"~{round(200 / reporting_divider, 1) if reporting_divider else 0} Hz, "
            f"{total_ranges} ranges, {total_bytes} B, {total_bps} B/s "
            f"(peak {peak_budget_pct}% of WiFi budget) — "
            f"{burst_result.note}",
            flush=True
        )

        # Wait for ALL expected slot schemas. The legacy
        # _pending_preset_slot is left None so single-mode wakeups don't
        # fire spuriously; the burst wakeup is fully driven by the set.
        expected_slots = [s for s, _ in burst_result.requests]
        self._preset_schema_event.clear()
        self._pending_preset_slot = None
        self._pending_preset_id = None
        self._pending_preset_slots = set(expected_slots)

        arrived = self._preset_schema_event.wait(timeout=schema_timeout)
        self._pending_preset_slot = None
        self._pending_preset_id = None
        self._pending_preset_slots = set()

        # Collect every schema the RX thread registered. In burst
        # strategy this is one entry; in multi-slot there are N.
        schemas: Dict[int, Any] = {}
        with self._stream_lock:
            for s in expected_slots:
                if s in self._stream_schemas:
                    schemas[s] = self._stream_schemas[s]

        if not arrived:
            missing = [s for s in expected_slots if s not in schemas]
            print(
                f"[wifi_bridge] Warning: {burst_result.strategy} preset {name_str} "
                f"timed out after {schema_timeout}s — missing 0x08 reply for slot(s) "
                f"{missing}; 0x09+slot frames will be decoded as raw float32 channels",
                file=sys.stderr, flush=True
            )

        # Aggregate preset_ids + names from the per-request results, in
        # the order the burst function chose (which is input order with
        # duplicates removed).
        aggregate_ids: list = []
        aggregate_names: list = []
        for r in burst_result.results:
            aggregate_ids.extend(r.preset_ids)
            aggregate_names.extend(r.preset_names)

        return {
            "ids":           aggregate_ids,
            "names":         aggregate_names,
            "strategy":      burst_result.strategy,
            "divider":       reporting_divider,
            "hz":            round(200 / reporting_divider, 1) if reporting_divider else 0.0,
            "n_ranges":      total_ranges,
            "total_bytes":   total_bytes,
            "bps":           total_bps,
            "budget_pct":    peak_budget_pct,
            "available_bps": min(
                (r.available_bps for r in burst_result.results), default=0,
            ),
            "schemas":       schemas,
            # Legacy single-mode field. ``schema`` is the first registered
            # schema if any; None if all expected replies timed out.
            "schema":        next(iter(schemas.values()), None),
            "requests":      burst_result.requests,
        }

    def subscribe_slot(
        self,
        slot: int,
        divider: int,
        ranges: Optional[list] = None,
        transport: int = 1,
    ) -> bytes:
        """Send ONE 0x21 request for ``slot``. ``divider=0`` stops the slot.

        ``ranges`` holds DWARF names (resolved against the ELF) or
        ``StreamRange`` objects. Names/fmts are remembered so the 0x08
        schema reply decodes with named, correctly-typed channels.
        Returns the request bytes; exactly one datagram is sent.
        """
        from ground_station.livewatch.stream import build_stream_request, StreamRange

        stream_ranges = []
        for r in ranges or []:
            if isinstance(r, str):
                self._ensure_preset_resolver()
                symbol = self._preset_resolver.resolve(r)
                r = StreamRange(address=symbol.address, size=symbol.size,
                                count=1, name=r, fmt=symbol.fmt)
            stream_ranges.append(r)

        # Fresh subscribe cycle: clear this slot's stale schema, stream
        # stats and lifecycle state BEFORE sending. This cleanup lives in
        # subscribe_slot (not _send_subscribe_bytes) so a batch-release
        # send inside _handle_schema_frame does NOT wipe the schema that
        # was just registered (S3A fix). Other slots are untouched.
        with self._stream_lock:
            self._stream_schemas.pop(slot, None)
            self._stream_stats.pop(slot, None)
            self._slot_states[slot] = "planned"
            if divider:
                self._pending_schema_ranges[slot] = tuple(stream_ranges)
            else:
                self._pending_schema_ranges.pop(slot, None)

        return self._send_subscribe_bytes(
            slot, divider, stream_ranges, transport=transport,
        )

    def _send_subscribe_bytes(
        self,
        slot: int,
        divider: int,
        ranges: list,
        transport: int = 1,
        request_id: Optional[int] = None,
    ) -> bytes:
        """Build and ship the 0x21 request for ``slot``, advancing its
        lifecycle state to ``sent``. Returns the request bytes.

        Used both directly by :meth:`subscribe_slot` (fresh cycle start) and
        by the RX thread to release a deferred batch. It deliberately does
        NOT clear ``_stream_schemas`` / ``_stream_stats`` -- only
        ``subscribe_slot`` does, so a schema reply that triggers a release
        does not erase the schema it just registered.
        """
        from ground_station.livewatch.stream import build_stream_request

        request = build_stream_request(
            ranges=ranges,
            divider=divider,
            transport=transport,
            slot=slot,
        )
        self._slot_states[slot] = "sent"
        self._wifi_send.sendto(request, (self._wifi_host, self._wifi_port))
        return request

    def subscribe_preview(
        self,
        slot: int = 0,
        divider: int = 1,
        ranges: Optional[list] = None,
    ) -> dict:
        """Preview which DWARF names a /subscribe request would resolve to.

        Validation-only: never sends bytes, never touches
        ``_pending_schema_ranges``. Slot/divider rules mirror
        :meth:`subscribe_slot` so an "ok" preview means the send would
        build. Returns ``slot``, ``divider``, resolved ``ranges``,
        ``unresolved`` names, ``var_count``, ``expected_rate_hz``.
        """
        if slot not in (0, 1, 2, 3):
            raise ValueError(
                f"slot {slot} outside known slot set (allowed: 0..3)"
            )
        if not 0 <= divider <= 255:
            raise ValueError(f"divider {divider} outside 0..255")
        if divider == 0:
            # Stop request — no ranges needed.
            return {
                "slot": slot,
                "divider": 0,
                "ranges": [],
                "unresolved": [],
                "var_count": 0,
                "expected_rate_hz": 0.0,
            }

        # Slot 0 with no explicit ranges: preview the dashboard layout.
        if slot == 0 and not ranges:
            preview_ranges = [
                entry[0] if isinstance(entry, tuple) else entry
                for entry in DASHBOARD_FRAME_A_VARS
            ]
        else:
            if not ranges:
                raise ValueError(
                    f"slot {slot} requires explicit ranges; no preset defined "
                    "for non-zero slots"
                )
            preview_ranges = list(ranges)

        resolved_names: list = []
        unresolved: list = []
        total_payload_bytes = 0
        expected_hz = self._expected_rate_for_slot(slot, divider)
        for r in preview_ranges:
            if isinstance(r, str):
                try:
                    self._ensure_preset_resolver()
                    sym = self._preset_resolver.resolve(r)
                    resolved_names.append(r)
                    # Symbol.size is the DWARF byte size of the variable
                    # (mirrored from _sizeof in livewatch/symbols.py). The
                    # subscribe protocol only accepts 1/2/4-byte members, so
                    # this sum is the honest payload the FC will stream.
                    total_payload_bytes += getattr(sym, "size", 0)
                except Exception:
                    unresolved.append(r)
                continue
            # Pre-built StreamRange — already resolved by the caller.
            name = getattr(r, "name", None) or f"<StreamRange@{id(r):x}>"
            resolved_names.append(name)
            total_payload_bytes += getattr(r, "size", 0)

        # Projected on-wire cost of the subscription, using the same
        # arithmetic as the firmware transport (stream_bps: 12 B frame
        # overhead + payload, at the 100 Hz Send_Task contract). This is a
        # *plan* the slot-manager shows against the link budget before the
        # user clicks Subscribe; the live used-bytes/s is measured on real
        # RX frames in the bandwidth panel (see docs/dashboard-platform/
        # shell/plugins/bandwidth-panel.js, WIFI_LINK_CAPACITY_BPS).
        projected_bps = (FRAME_OVERHEAD + total_payload_bytes) * expected_hz
        return {
            "slot": slot,
            "divider": divider,
            "ranges": resolved_names,
            "unresolved": unresolved,
            "var_count": len(resolved_names),
            "expected_rate_hz": expected_hz,
            "payload_bytes": total_payload_bytes,
            "frame_bytes": FRAME_OVERHEAD + total_payload_bytes,
            "projected_bps": int(projected_bps),
        }

    @staticmethod
    def _expected_rate_for_slot(slot: int, divider: int) -> float:
        """Expected on-wire rate: 100 Hz Send_Task contract / divider.

        Uses the 100 Hz "by design" contract (rate_planner /
        firmware_contract), matching this endpoint's baseline test;
        the firmware header says 200 nominal (~80 measured MIXED).
        """
        if divider <= 0:
            return 0.0
        SUBSCRIBE_SEND_TASK_HZ = 100
        return min(SUBSCRIBE_SEND_TASK_HZ / divider, SUBSCRIBE_SEND_TASK_HZ)

    def clear_manifest_schemas(self) -> None:
        """Drop all per-slot schemas (called when the operator stops a layout)."""
        with self._stream_lock:
            self._stream_schemas.clear()

    def get_telemetry(self) -> tuple[Dict[str, Any], float]:
        """Latest decoded telemetry + timestamp (thread-safe)."""
        with self._telem_lock:
            return dict(self._last_telem), self._last_telem_t

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    def _rx_loop(self) -> None:
        """Read WiFi UDP, decode telemetry frames, publish JSON to dashboard."""
        rx_buf = bytearray()
        last_warn = 0.0

        while not self._stop.is_set():
            try:
                data, addr = self._wifi.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                if time.monotonic() - last_warn > 5.0:
                    last_warn = time.monotonic()
                    print("[wifi_bridge] WiFi socket recv error, retrying...", file=sys.stderr)
                continue

            rx_buf.extend(data)

            while len(rx_buf) > 0:
                result = self._parse_one(rx_buf)
                if result is None:
                    break  # partial frame: wait for more data
                tag, payload = result
                if tag == "transaction":
                    self._transaction_results.put(payload)
                else:
                    self._publish_telem(tag, payload)
                    # Feed decoded telemetry to service.ingest_decoded() if callback is registered.
                    if self._on_telemetry is not None:
                        try:
                            self._on_telemetry(tag, payload)
                        except Exception:
                            pass

    def _parse_one(self, buf: bytearray) -> tuple | None:
        """Parse one complete frame from buf.

        Three families appear on USART3 TX:
          MAVLink v1.0      → [0xFE][LEN][SEQ][SYS][COMP][MSG_LO][MSG_HI]  = msgs 10001-10003
          Custom_DataBuf[68] → [0xAA][0xAA][0x01][LEN_HI][LEN_LO][NB]       = Frame A
          Buf_Telemetry_UART4 → [0xAA][0xBB][TYPE][LEN_HI][LEN_LO][NB]      = B / ID / C

        Returns (tag, payload) where tag is "a", "b", "id", "c", "mav_w", "mav_e", "mav_c".
        """

        # --- MAVLink v1.0: 0xFE magic byte (checked first — unambiguous) ---
        if len(buf) >= 8 and buf[0] == 0xFE:
            mav_len = buf[1]
            msg_id = buf[5] | (buf[6] << 8)
            total = 8 + mav_len + 2
            if len(buf) >= total:
                frame = bytes(buf[:total])
                del buf[:total]
                if msg_id == 10001:
                    return "mav_w", self._decode_mavlink_mrac_weights(frame)
                elif msg_id == 10002:
                    return "mav_e", self._decode_mavlink_ekf_states(frame)
                elif msg_id == 10003:
                    return "mav_c", self._decode_mavlink_ctrl_debug(frame)
                return None  # unknown MAVLink message ID

        # --- Frame A: 0xAA 0xAA 0x01 (Custom_DataBuf, 68 B total) ---
        if len(buf) >= 68 and buf[0] == 0xAA and buf[1] == 0xAA and buf[2] == 0x01:
            frame = bytes(buf[:68])
            del buf[:68]
            return "a", self._decode_frame_a(frame)

        # --- Variable-length frames: 0xAA 0xBB (Buf_Telemetry_UART4) ---
        if len(buf) >= 6 and buf[0] == 0xAA and buf[1] == 0xBB:
            frame_type = buf[2]
            payload_len = (buf[3] << 8) | buf[4]
            # Tail bytes differ by family: subscribe/Frame C frames carry a
            # 2-byte CRC16; 0x08 schema and legacy frames carry 1 XOR byte;
            # transaction results (0x30..0x32) carry 1 XOR byte but their
            # length field excludes it (len = 6 + payload_len).
            if 0x09 <= frame_type <= 0x0C or frame_type == 0x06:
                total_len = 6 + payload_len + 2
            elif frame_type in (0x30, 0x31, 0x32):
                total_len = 6 + payload_len
            else:
                total_len = 6 + payload_len + 1
            if len(buf) >= total_len:
                frame = bytes(buf[:total_len])
                del buf[:total_len]
                if not 0x08 <= frame_type <= 0x0C:
                    self._check_resubscribe()
                if frame_type == 0x01:
                    return "a", self._decode_frame_a_uart4(frame)
                elif frame_type == 0x02:
                    return "b", self._decode_frame_b(frame)
                elif frame_type == 0x03:
                    return "id", self._decode_frame_id(frame)
                elif frame_type == 0x06:
                    return "c", self._decode_frame_c(frame)
                elif frame_type == 0x08:
                    # Schema reply: decode and register for this slot
                    decoded_slot = self._handle_schema_frame(frame)
                    # Wake up subscribe_preset() if this is one of the pending
                    # preset slots. Two paths to preserve:
                    #   - Legacy single-slot path: _pending_preset_slot set,
                    #     matches the slot directly.
                    #   - Burst-mode path: _pending_preset_slots is a set of
                    #     expected slots; remove this one and set the event
                    #     only when ALL expected slots have responded.
                    if decoded_slot is not None:
                        if self._pending_preset_slot == decoded_slot:
                            self._preset_schema_event.set()
                        if decoded_slot in self._pending_preset_slots:
                            self._pending_preset_slots.discard(decoded_slot)
                            if not self._pending_preset_slots:
                                self._preset_schema_event.set()
                    return None  # don't forward schema to dashboard
                elif frame_type in (0x30, 0x31, 0x32):
                    try:
                        result = parse_result_parts(frame_type, frame[5], frame[6:-1])
                    except Exception:
                        return None
                    return "transaction", result
                # Subscribe data frames: TYPE = 0x09 + slot.
                # Slot 0 is reserved for livewatch's auto-subscribe; the
                # data frame type would be 0x09 but the FC emits only 0x0A
                # onwards here. We accept all four.
                elif 0x09 <= frame_type <= 0x0C:
                    slot = frame_type - 0x09
                    decoded = self._decode_stream_frame(slot, frame)
                    if decoded is not None:
                        self._last_stream_rx = time.monotonic()
                        # Two downstream sinks: dashboard JSON mirror and
                        # VoFA+ JustFloat. The latter only fires if a
                        # schema was registered for this slot.
                        self._forward_vofa(slot, decoded["values"])
                        # Publish ALL decoded slot-0 variables to "a" (not just the mapped
                        # subset). This ensures that when the subscribe stream arrives it
                        # does not create holes: every field the FC streams appears in "a"
                        # under its raw DWARF name, so nothing is lost when subscribe
                        # overwrites UART5 Frame A data between Frame A bursts.
                        # The sidebar reads named keys (status.*, mrac.*) via the mapping
                        # below; raw names coexist so no data is silently dropped.
                        if slot == 0 and decoded.get("names"):
                            # Merge mapped sidebar keys (for named dashboard reads) with
                            # raw names (so no field is lost).  Use round-trip-safe
                            # formatting: integer fields as int, floats with 3 decimal places.
                            sidebar_payload = self._slot0_to_sidebar(
                                decoded["names"], decoded["values"]
                            )
                            # Also publish raw names directly so unmapped vars are not lost
                            for n, v in zip(decoded["names"], decoded["values"]):
                                sidebar_payload[n] = round(float(v), 3)
                            if sidebar_payload:
                                self._publish_telem("a", sidebar_payload)
                        return "s" + str(slot), decoded["json"]
                    return None
                else:
                    return None  # unknown frame type — wait for more data

        # --- Raw 16-byte JustFloat attitude (usart3_send fallback) ---
        # When no subscribe stream and no telemetry mirror is active.
        if len(buf) >= 16 and buf[12:16] == b"\x00\x00\x80\x7f":
            raw12 = bytes(buf[:12])  # rol, pit, yaw as LE float32
            del buf[:12]
            del buf[:4]  # JustFloat terminator
            rol, pit, yaw = struct.unpack("<3f", raw12)
            self._check_resubscribe()
            return "a", {
                "status.roll_deg": round(rol, 3),
                "status.pitch_deg": round(pit, 3),
                "status.yaw_deg": round(yaw, 3),
            }

        # --- Partial frame: cap buffer to avoid unbounded growth ---
        if len(buf) > 1024:
            del buf[:1]  # resync by skipping one byte
            return self._parse_one(buf)

        return None  # wait for more data

    def _publish_telem(self, tag: str, payload: Dict[str, Any]) -> None:
        """Store latest telemetry and send JSON mirror to dashboard.

        Tags are MERGED into ``self._last_telem`` rather than overwritten,
        so a Frame A burst followed by a subscribe-stream slot-0 frame does
        not wipe the Frame A status from the periodic mirror. ``_telem_loop``
        re-sends the full dict every 100 ms so the dashboard always has the
        freshest version of every known tag.
        """
        t = time.monotonic()
        with self._telem_lock:
            # Merge: same tag replaces its dict; other tags survive.
            self._last_telem[tag] = payload
            self._last_telem_t = t
        try:
            msg = json.dumps({tag: payload}, separators=(",", ":")).encode("utf-8")
            self._telem_udp.sendto(msg, ("127.0.0.1", self._telem_udp_port))
        except OSError:
            pass

    @staticmethod
    def _slot0_to_sidebar(names, values):
        """Map boot-default slot 0 names to Frame A sidebar keys.

        The dashboard's sidebar reads ``a["status.*"]`` keys derived from the
        UART5 Frame A layout. The boot-default subscribe stream (slot 0) emits
        the same physical quantities under their DWARF paths (e.g.
        ``imu_data.rol`` -> ``status.roll_deg``). This keeps the sidebar
        populated when the host is connected via WiFi.

        Extended 2026-09-12 to cover the full legacy Frame A payload so the
        dashboard's MRAC bar monitor (lines 2089-2099 of dashboard.py) and
        the status flags update over the subscribe path on slot 0. The
        mapping mirrors ``ground_station/comm/wifi_bridge.py::_decode_frame_a``:
            8 x f32 -- MRAC e + u_ad for pitch/roll/yaw/z
            9 x u8  -- status flags (arm, flymode, sbus_lost, twc_*, ...)

        Only keys present in the schema are returned -- missing ones stay
        absent so the sidebar's "?" rendering remains the truth-of-the-wire.
        """
        mapping = {
            # Attitude
            "imu_data.rol":          "status.roll_deg",
            "imu_data.pit":          "status.pitch_deg",
            "imu_data.yaw":          "status.yaw_deg",
            # Status flags (9 -- mirrors _decode_frame_a 9-byte status block).
            # NOTE: TWC is a 36-byte struct on the wire, but the subscribe
            # protocol only accepts size 1/2/4 -- we read `TWC.execute` (u32)
            # and treat it as a 0/1 flag here.
            "DroneStatus.ARM_Status": "status.arm",
            "DroneStatus.FlyMode":   "status.flymode",
            "sbus_lost":             "status.sbus_lost",
            "TWC.execute":           "status.twc_execute",
            "TWC_arrived":           "status.twc_arrived",
            "s_authority":           "status.rc_authority",
            "g_of_hold_active":     "status.of_hold",
            "g_estimator_ready":     "status.estimator_ready",
            # Battery
            "real_voltage":          "status.vbat",
            # MRAC bars (8 -- mirrors _decode_frame_a 32-byte MRAC block).
            # Key names MUST match dashboard.py:_update_monitor_ui:
            # ("mrac.pitch.e", "mrac.pitch.u_ad"), ... ("mrac.z.e", "mrac.z.u_ad")
            "mrac_state.pitch.e":    "mrac.pitch.e",
            "mrac_state.pitch.u_ad": "mrac.pitch.u_ad",
            "mrac_state.roll.e":     "mrac.roll.e",
            "mrac_state.roll.u_ad":  "mrac.roll.u_ad",
            "mrac_state.yaw.e":      "mrac.yaw.e",
            "mrac_state.yaw.u_ad":   "mrac.yaw.u_ad",
            "mrac_state.z_rate.e":   "mrac.z.e",
            "mrac_state.z_rate.u_ad": "mrac.z.u_ad",
            "s_ekf.x[0]": "ekf.vel_x",
            "s_ekf.x[1]": "ekf.vel_y",
            "s_ekf.x[2]": "ekf.vel_z",
            "s_ekf.x[3]": "ekf.bias_accel_x",
            "s_ekf.x[4]": "ekf.bias_accel_y",
            "s_ekf.x[5]": "ekf.bias_accel_z",
            "s_ekf.x[6]": "ekf.bias_gyro_x",
            "s_ekf.x[7]": "ekf.bias_gyro_y",
            "s_ekf.x[8]": "ekf.bias_gyro_z",
        }
        out: Dict[str, float] = {}
        for n, v in zip(names, values):
            key = mapping.get(n)
            if key is not None:
                # Integer status fields round-trip better at 0 decimals.
                if key in (
                    "status.arm", "status.flymode",
                    "status.sbus_lost", "status.twc_execute", "status.twc_arrived",
                    "status.rc_authority", "status.of_hold", "status.estimator_ready",
                ):
                    out[key] = round(float(v), 0)
                else:
                    out[key] = round(float(v), 3)
        return out

    def _cmd_loop(self) -> None:
        """Read dashboard JSON commands, forward as 0xCC 0xDD frames over WiFi."""
        while not self._stop.is_set():
            try:
                data, addr = self._cmd_udp.recvfrom(65535)
            except socket.timeout:
                # No command waiting: flush queued programmatic commands
                self._flush_queue()
                continue
            except OSError:
                continue

            if data == b"ping":
                # Reply to the sender's address, not to our own socket.
                # The old code hard-coded ("127.0.0.1", self._cmd_udp_port) which
                # made the pong land on our own socket and never reach the
                # dashboard -- the launcher and the dashboard's auto-connect
                # both timed out for this reason.
                self._cmd_udp.sendto(b"pong", addr)
                continue

            try:
                cmd = json.loads(data.decode("utf-8"))
            except Exception:
                continue

            self._cmd_queue.put_nowait(cmd)
            self._flush_queue()

    def _flush_queue(self) -> None:
        """Send all queued commands over WiFi."""
        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            self._send_cmd_frame(
                int(cmd["cmd_id"]),
                int(cmd["index"]),
                float(cmd["value"]),
            )

    def _send_keepalive_nudge(self) -> None:
        """Re-aim the MicoAir downlink at this host on a timer.

        The MicoAir routes USART3 telemetry to the source of the most recent
        uplink datagram and its route mapping idles out after several minutes
        of silence (observed ~8-10 min). Sending the same 1-byte 0x00 nudge
        that start() uses re-aims it at our socket, which is cheap (1 B every
        keepalive_interval s) and harmless to the FC (a no-op command frame).
        """
        w = getattr(self, "_wifi", None)
        if w is None:
            return
        now = time.monotonic()
        if now - self._last_nudge < self._keepalive_interval:
            return
        try:
            w.sendto(b"\x00", (self._nudge_host, self._nudge_port))
        except OSError:
            pass
        self._last_nudge = now

    def _telem_loop(self) -> None:
        """Periodically re-send latest telemetry to the dashboard mirror port.

        Also refreshes the MicoAir downlink aim by re-sending the 1-byte
        nudge every `keepalive_interval` s. Without this the module's route
        to this host idles out after ~8-10 min and streaming silently stops;
        see docs/skills/micoair-connect.md "The nudge requirement".
        """
        while not self._stop.is_set():
            time.sleep(0.1)
            # Refresh the downlink route BEFORE the mirror check so the
            # keep-alive still fires even when no telemetry has arrived yet.
            self._send_keepalive_nudge()
            with self._telem_lock:
                if not self._last_telem:
                    continue
                telem = dict(self._last_telem)
            try:
                msg = json.dumps(telem, separators=(",", ":")).encode("utf-8")
                self._telem_udp.sendto(msg, ("127.0.0.1", self._telem_udp_port))
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Frame parsing
    # ------------------------------------------------------------------

    def _decode_frame_b(self, frame: bytes) -> Dict[str, Any]:
        """Decode 0xAA 0xBB Frame B (variable length) into a dict.

        Layout: [0xAA][0xBB][0x02][LEN_HI][LEN_LO][MAX_NB]
                [MRAC: 4 axes × (MAX_NB × float32 + u_nom float32 + xm float32)]
                [PID: 12 loops × (FB/Des/U × float32)]
                [tail: v3/v13 path + status]
        Keys match SerialBridge._unpack_frame_b exactly.
        """
        try:
            header = frame[:6]
            payload = frame[6:]
            payload_len = (header[3] << 8) | header[4]
            max_nb = header[5]

            # MRAC: 4 axes × (MAX_NB × 4 + 4 + 4) = 4 × (4N + 8) bytes
            # PID: 12 × 3 × 4 = 144 bytes
            total_floats = 4 * (max_nb + 2) + 36
            main_len = total_floats * 4
            tail_len = payload_len - main_len
            has_vbat = tail_len == 26

            vals = list(struct.unpack_from("<" + "f" * total_floats, payload, 0))
            tail = payload[main_len:payload_len]

            result: Dict[str, Any] = {}

            # MRAC: 4 axes × (theta_0..theta_{N-1}, u_nom, xm)
            axis_names = ["pitch", "roll", "yaw", "z"]
            idx = 0
            for ax in range(4):
                for b in range(max_nb):
                    result[f"mrac.{axis_names[ax]}.theta_{b}"] = float(vals[idx])
                    idx += 1
                result[f"mrac.{axis_names[ax]}.u_nom"] = float(vals[idx])
                idx += 1
                result[f"mrac.{axis_names[ax]}.xm"] = float(vals[idx])
                idx += 1

            # PID: 12 loops × (FB, Des, U)
            pid_names = [
                "pitch", "roll", "yaw", "gyrox", "gyroy", "gyroz",
                "z_rate", "locx", "locy", "z_pos", "locxs", "locys",
            ]
            for name in pid_names:
                result[f"pid.{name}.FB"] = float(vals[idx]); idx += 1
                result[f"pid.{name}.Des"] = float(vals[idx]); idx += 1
                result[f"pid.{name}.U"] = float(vals[idx]); idx += 1

            # Path tail (26 or 30 bytes)
            if has_vbat:
                (apm_u8, twc_tx, twc_ty, twc_tz, sin_te,
                 circ_th, twc_arr_u8, vbat) = struct.unpack("<BfffffBf", tail)
                result["status.vbat"] = float(vbat)
            elif tail_len == 30:
                (apm_u8, twc_tx, twc_ty, twc_tz, sin_te, circ_th, twc_arr_u8, vbat,
                 of_hold_u8, estimator_ready_u8, _f0, _f1) = struct.unpack("<BfffffBfBBBB", tail)
                result["status.vbat"] = float(vbat)
                result["status.of_hold"] = float(of_hold_u8)
                result["status.estimator_ready"] = float(estimator_ready_u8)
            else:
                (apm_u8, twc_tx, twc_ty, twc_tz, sin_te, circ_th, twc_arr_u8
                 ) = struct.unpack("<BfffffB", tail)

            result["path.active_path_mode"] = float(apm_u8)
            result["path.twc_target_x"] = float(twc_tx)
            result["path.twc_target_y"] = float(twc_ty)
            result["path.twc_target_z"] = float(twc_tz)
            result["path.sinusoid_t_elapsed"] = float(sin_te)
            result["path.circle_theta"] = float(circ_th)
            result["path.twc_arrived"] = float(twc_arr_u8)

            return result
        except struct.error:
            return {}

    def _decode_frame_c(self, frame: bytes) -> Dict[str, Any]:
        """Decode 0xAA 0xBB Frame C (body rate + position, 50 Hz).

        Layout: rol,pit,yaw(f32,deg) | gyro_x/y/z(f32,rad/s)
                | earth_x/y(f32,m) | altitude(f32,m)
                | rpm[0..3](u16) | seq(u16)
        Keys match SerialBridge._unpack_frame_c exactly.
        """
        try:
            payload = frame[6:]
            rol, pit, yaw = struct.unpack_from("<3f", payload, 0)
            gx, gy, gz = struct.unpack_from("<3f", payload, 12)
            ex, ey = struct.unpack_from("<2f", payload, 24)
            alt = struct.unpack_from("<f", payload, 32)[0]
            rpm_vals = struct.unpack_from("<4H", payload, 36)
            seq = struct.unpack_from("<H", payload, 44)[0]
            res = {
                "c.roll": rol, "c.pitch": pit, "c.yaw": yaw,
                "c.gyro_x": gx, "c.gyro_y": gy, "c.gyro_z": gz,
                "c.earth_x": ex, "c.earth_y": ey, "c.altitude": alt,
                "c.rpm": list(rpm_vals),
                "c.seq": float(seq),
            }
            res.update(_rpm_scalar_keys(list(rpm_vals)))
            return res
        except struct.error:
            return {}

    def _decode_frame_id(self, frame: bytes) -> Dict[str, Any]:
        """Decode 0xAA 0xBB Frame ID (100 Hz, status + counters).

        Layout: u32 counter | u8 axis_id | r,x,u_nom,u_ad,xm (axis only)
                | f32 sysid_dither | f32 voltage | u8 ARM | u8 mode | u8 FSM
        Keys match SerialBridge._unpack_frame_id exactly.
        """
        try:
            payload = frame[6:]
            counter = struct.unpack_from("<I", payload, 0)[0]
            arm = payload[4]
            mode = payload[5]
            rc_auth = payload[6]
            of_hold = payload[7]
            est_ready = payload[8]
            sysid_state = payload[9]
            return {
                "id.counter": float(counter),
                "status.arm": float(arm),
                "status.mode": float(mode),
                "status.flymode": float(mode),
                "status.rc_authority": float(rc_auth),
                "status.of_hold": float(of_hold),
                "status.estimator_ready": float(est_ready),
                "status.sysid_state": float(sysid_state),
            }
        except struct.error:
            return {}

    # ------------------------------------------------------------------
    # MAVLink v1.0 custom message decoders
    # ------------------------------------------------------------------

    def _crc16_x25(self, data: bytes, init: int = 0xFFFF) -> int:
        """CRC-16/X.25 over a byte buffer — mirrors BSP/mavlink_crc.c."""
        crc = init
        for b in data:
            crc ^= b
            crc = ((crc >> 8) ^ self._CRC_X25_TABLE[crc & 0xFF]) & 0xFFFF
        return crc ^ 0xFFFF

    _CRC_X25_TABLE = (
        0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
        0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7,
        0x1081, 0x0108, 0x3393, 0x221A, 0x56A5, 0x472C, 0x75B7, 0x643E,
        0x9CC9, 0x8D40, 0xBFDB, 0xAE52, 0xDAED, 0xCB64, 0xF9FF, 0xE876,
        0x2102, 0x308B, 0x0210, 0x1399, 0x6726, 0x76AF, 0x4434, 0x55BD,
        0xAD4A, 0xBCC3, 0x8E58, 0x9FD1, 0xEB6E, 0xFAE7, 0xC87C, 0xD9F5,
        0x3183, 0x200A, 0x1291, 0x0318, 0x77A7, 0x662E, 0x54B5, 0x453C,
        0xBDCB, 0xAC42, 0x9ED9, 0x8F50, 0xFBEF, 0xEA66, 0xD8FD, 0xC974,
        0x4204, 0x538D, 0x6116, 0x709F, 0x0420, 0x15A9, 0x2732, 0x36BB,
        0xCE4C, 0xDFC5, 0xED5E, 0xFCD7, 0x8868, 0x99E1, 0xAB7A, 0xBAF3,
        0x5285, 0x430C, 0x7197, 0x601E, 0x14A1, 0x0528, 0x37B3, 0x263A,
        0xDECD, 0xCF44, 0xFDDF, 0xEC56, 0x98E9, 0x8960, 0xBBFB, 0xAA72,
        0x6306, 0x728F, 0x4014, 0x519D, 0x2522, 0x34AB, 0x0630, 0x17B9,
        0xEF4E, 0xFEC7, 0xCC5C, 0xDDD5, 0xA96A, 0xB8E3, 0x8A78, 0x9BF1,
        0x7387, 0x620E, 0x5095, 0x411C, 0x35A3, 0x242A, 0x16B1, 0x0738,
        0xFFCF, 0xEE46, 0xDCDD, 0xCD54, 0xB9EB, 0xA862, 0x9AF9, 0x8B70,
        0x8408, 0x9581, 0xA71A, 0xB693, 0xC22C, 0xD3A5, 0xE13E, 0xF0B7,
        0x0840, 0x19C9, 0x2B52, 0x3ADB, 0x4E64, 0x5FED, 0x6D76, 0x7CFF,
        0x9489, 0x8500, 0xB79B, 0xA612, 0xD2AD, 0xC324, 0xF1BF, 0xE036,
        0x18C1, 0x0948, 0x3BD3, 0x2A5A, 0x5EE5, 0x4F6C, 0x7DF7, 0x6C7E,
        0xA50A, 0xB483, 0x8618, 0x9791, 0xE32E, 0xF2A7, 0xC03C, 0xD1B5,
        0x2942, 0x38CB, 0x0A50, 0x1BD9, 0x6F66, 0x7EEF, 0x4C74, 0x5DFD,
        0xB58B, 0xA402, 0x9699, 0x8710, 0xF3AF, 0xE226, 0xD0BD, 0xC134,
        0x39C3, 0x284A, 0x1AD1, 0x0B58, 0x7FE7, 0x6E6E, 0x5CF5, 0x4D7C,
        0xC60C, 0xD785, 0xE51E, 0xF497, 0x8028, 0x91A1, 0xA33A, 0xB2B3,
        0x4A44, 0x5BCD, 0x6956, 0x78DF, 0x0C60, 0x1DE9, 0x2F72, 0x3EFB,
        0xD68D, 0xC704, 0xF59F, 0xE416, 0x90A9, 0x8120, 0xB3BB, 0xA232,
        0x5AC5, 0x4B4C, 0x79D7, 0x685E, 0x1CE1, 0x0D68, 0x3FF3, 0x2E7A,
        0xE70E, 0xF687, 0xC41C, 0xD595, 0xA12A, 0xB0A3, 0x8238, 0x93B1,
        0x6B46, 0x7ACF, 0x4854, 0x59DD, 0x2D62, 0x3CEB, 0x0E70, 0x1FF9,
        0xF78F, 0xE606, 0xD49D, 0xC514, 0xB1AB, 0xA022, 0x92B9, 0x8330,
        0x7BC7, 0x6A4E, 0x58D5, 0x495C, 0x3DE3, 0x2C6A, 0x1EF1, 0x0F78,
    )

    def _decode_mavlink(self, frame: bytes) -> tuple[int, bytes, bytes]:
        """Parse a MAVLink v1.0 frame into (msg_id, payload, sys_id).

        Raises ValueError if the CRC does not match.
        """
        if frame[0] != 0xFE:
            raise ValueError("not a MAVLink v1.0 frame")
        mav_len = frame[1]
        seq = frame[2]
        sys_id = frame[3]
        comp_id = frame[4]
        msg_id = frame[5] | (frame[6] << 8)
        payload = frame[8:8 + mav_len]
        crc_lo, crc_hi = frame[8 + mav_len], frame[9 + mav_len]
        crc_received = crc_lo | (crc_hi << 8)
        # CRC covers [MSG_LO, MSG_HI, LEN, payload] — no STX/SEQ/SYS/COMP
        crc_data = bytes([frame[5], frame[6], frame[1]]) + payload
        crc_computed = self._crc16_x25(crc_data)
        if crc_computed != crc_received:
            raise ValueError(
                f"MAVLink CRC mismatch: got 0x{crc_received:04X}, "
                f"computed 0x{crc_computed:04X}"
            )
        return msg_id, payload, sys_id

    def _decode_mavlink_mrac_weights(self, frame: bytes) -> Dict[str, Any]:
        """Decode MAVLink MSG 10001 — MRAC_WEIGHTS.

        Payload: time_usec(u32) + theta[4][6](4×6×f32) + u_nom[4](f32) + xm[4](f32)
        Layout mirrors BSP/mavlink_custom.h MAV_MRAC_WEIGHTS_PAYLOAD_LEN=132.
        """
        try:
            msg_id, payload, sys_id = self._decode_mavlink(frame)
            if msg_id != 10001:
                return {}
            time_usec, = struct.unpack_from("<I", payload, 0)
            vals = struct.unpack_from("<24f", payload, 4)  # 4 axes × 6 theta
            u_nom = struct.unpack_from("<4f", payload, 100)  # offset 4 + 96
            xm = struct.unpack_from("<4f", payload, 116)    # offset 4 + 112
            result: Dict[str, Any] = {"mav.time_usec": float(time_usec)}
            axis_names = ["pitch", "roll", "yaw", "z"]
            for a in range(4):
                for b in range(6):
                    result[f"mrac.{axis_names[a]}.theta_{b}"] = float(vals[a * 6 + b])
                result[f"mrac.{axis_names[a]}.u_nom"] = float(u_nom[a])
                result[f"mrac.{axis_names[a]}.xm"] = float(xm[a])
            return result
        except (struct.error, ValueError):
            return {}

    def _decode_mavlink_ekf_states(self, frame: bytes) -> Dict[str, Any]:
        """Decode MAVLink MSG 10002 — EKF_STATES.

        Payload: time_usec(u32) + vel_body[3](f32) + accel_bias[3](f32)
                 + gyro_bias[3](f32) + euler[3](f32)
        Layout mirrors BSP/mavlink_custom.h MAV_EKF_STATES_PAYLOAD_LEN=52.
        """
        try:
            msg_id, payload, sys_id = self._decode_mavlink(frame)
            if msg_id != 10002:
                return {}
            time_usec, = struct.unpack_from("<I", payload, 0)
            vel_body = struct.unpack_from("<3f", payload, 4)
            accel_bias = struct.unpack_from("<3f", payload, 16)
            gyro_bias = struct.unpack_from("<3f", payload, 28)
            euler = struct.unpack_from("<3f", payload, 40)
            return {
                "mav.time_usec": float(time_usec),
                "ekf.vel_body_x": float(vel_body[0]),
                "ekf.vel_body_y": float(vel_body[1]),
                "ekf.vel_body_z": float(vel_body[2]),
                "ekf.accel_bias_x": float(accel_bias[0]),
                "ekf.accel_bias_y": float(accel_bias[1]),
                "ekf.accel_bias_z": float(accel_bias[2]),
                "ekf.gyro_bias_x": float(gyro_bias[0]),
                "ekf.gyro_bias_y": float(gyro_bias[1]),
                "ekf.gyro_bias_z": float(gyro_bias[2]),
                "ekf.roll_rad": float(euler[0]),
                "ekf.pitch_rad": float(euler[1]),
                "ekf.yaw_rad": float(euler[2]),
            }
        except (struct.error, ValueError):
            return {}

    def _decode_mavlink_ctrl_debug(self, frame: bytes) -> Dict[str, Any]:
        """Decode MAVLink MSG 10003 — CONTROL_DEBUG.

        Payload: time_usec(u32) + e[4](f32) + u_ad[4](f32) + r[4](f32)
                 + fsm_state(u16) + active_path(u16) + target[3](f32)
        Layout mirrors BSP/mavlink_custom.h MAV_CTRL_DEBUG_PAYLOAD_LEN=64.
        """
        try:
            msg_id, payload, sys_id = self._decode_mavlink(frame)
            if msg_id != 10003:
                return {}
            time_usec, = struct.unpack_from("<I", payload, 0)
            e_vals = struct.unpack_from("<4f", payload, 4)
            u_ad_vals = struct.unpack_from("<4f", payload, 20)
            r_vals = struct.unpack_from("<4f", payload, 36)
            fsm_state, active_path = struct.unpack_from("<HH", payload, 52)
            target = struct.unpack_from("<3f", payload, 56)
            axis_names = ["pitch", "roll", "yaw", "z"]
            result: Dict[str, Any] = {
                "mav.time_usec": float(time_usec),
                "ctrl.fsm_state": float(fsm_state),
                "ctrl.active_path": float(active_path),
                "ctrl.target_x": float(target[0]),
                "ctrl.target_y": float(target[1]),
                "ctrl.target_z": float(target[2]),
            }
            for a in range(4):
                result[f"ctrl.{axis_names[a]}.e"] = float(e_vals[a])
                result[f"ctrl.{axis_names[a]}.u_ad"] = float(u_ad_vals[a])
                result[f"ctrl.{axis_names[a]}.r"] = float(r_vals[a])
            return result
        except (struct.error, ValueError):
            return {}

    def _decode_frame_a(self, frame: bytes) -> Dict[str, Any]:
        """Decode 0xAA 0xAA Frame A (Custom_DataBuf, 68 B).

        Layout: [0xAA][0xAA][0x01][LEN_HI][LEN_LO][NB]
                [8×f32: MRAC pitch/roll/yaw/z e+u_ad]
                [8×u8: status]
        Keys match SerialBridge._unpack_frame_a exactly.
        """
        try:
            payload = frame[6:]
            if len(payload) == 38:
                vals = struct.unpack_from("<8fBBBBBBB", payload, 0)
                of_hold_u8, est_u8 = 0, 0
            else:
                vals = struct.unpack_from("<8fBBBBBBBBB", payload, 0)
                of_hold_u8 = vals[14]
                est_u8 = vals[15]
            p_e, p_u, r_e, r_u, y_e, y_u, z_e, z_u = vals[0:8]
            arm_u8, flymode_u8, sbus_lost_u8, twc_exec_u8, twc_arr_u8, rc_auth_u8, _proto = vals[8:15]
            return {
                "mrac.pitch.e": float(p_e), "mrac.pitch.u_ad": float(p_u),
                "mrac.roll.e": float(r_e), "mrac.roll.u_ad": float(r_u),
                "mrac.yaw.e": float(y_e), "mrac.yaw.u_ad": float(y_u),
                "mrac.z.e": float(z_e), "mrac.z.u_ad": float(z_u),
                "status.arm": float(arm_u8),
                "status.flymode": float(flymode_u8),
                "status.sbus_lost": float(sbus_lost_u8),
                "status.twc_execute": float(twc_exec_u8),
                "status.twc_arrived": float(twc_arr_u8),
                "status.rc_authority": float(rc_auth_u8),
                "status.of_hold": float(of_hold_u8),
                "status.estimator_ready": float(est_u8),
            }
        except struct.error:
            return {}

    def _decode_frame_a_uart4(self, frame: bytes) -> Dict[str, Any]:
        """Decode 0xAA 0xBB 0x01 Frame A (Buf_Telemetry_UART4 variant, sent over WiFi).

        Layout: [0xAA][0xBB][0x01][LEN_HI][LEN_LO][NB]
                [8×f32: MRAC pitch/roll/yaw/z e+u_ad = 32 B]
                [9×u8: status fields = 9 B]
        Total payload: 41 bytes (32 + 9).
        """
        try:
            payload = frame[6:]
            if len(payload) < 41:
                return {}
            vals = struct.unpack_from("<8fBBBBBBBBB", payload, 0)
            p_e, p_u, r_e, r_u, y_e, y_u, z_e, z_u = vals[0:8]
            arm_u8, flymode_u8, sbus_lost_u8, twc_exec_u8, twc_arr_u8, rc_auth_u8, of_hold_u8, est_u8, _proto = vals[8:17]
            return {
                "mrac.pitch.e": float(p_e), "mrac.pitch.u_ad": float(p_u),
                "mrac.roll.e": float(r_e), "mrac.roll.u_ad": float(r_u),
                "mrac.yaw.e": float(y_e), "mrac.yaw.u_ad": float(y_u),
                "mrac.z.e": float(z_e), "mrac.z.u_ad": float(z_u),
                "status.arm": float(arm_u8),
                "status.flymode": float(flymode_u8),
                "status.sbus_lost": float(sbus_lost_u8),
                "status.twc_execute": float(twc_exec_u8),
                "status.twc_arrived": float(twc_arr_u8),
                "status.rc_authority": float(rc_auth_u8),
                "status.of_hold": float(of_hold_u8),
                "status.estimator_ready": float(est_u8),
            }
        except struct.error:
            return {}

    # ------------------------------------------------------------------
    # Command forwarding
    # ------------------------------------------------------------------

    def _send_cmd_frame(self, cmd_id: int, index: int, value: float) -> None:
        """Build and send a 0xCC 0xDD command frame over WiFi to the FC."""
        body = bytes([0xCC, 0xDD, cmd_id, index]) + struct.pack("<f", value)
        crc = 0
        for b in body[2:]:
            crc ^= b
        frame = body + bytes([crc])
        try:
            sender = getattr(self, "_wifi_send", self._wifi)
            sender.sendto(frame, (self._wifi_host, self._wifi_port))
        except OSError as e:
            print(f"[wifi_bridge] send error: {e}", file=sys.stderr)

    def _send_transaction_frame(self, frame: bytes) -> None:
        """Send one versioned transaction envelope over the command socket."""
        try:
            sender = getattr(self, "_wifi_send", self._wifi)
            sender.sendto(frame, (self._wifi_host, self._wifi_port))
        except OSError as e:
            print(f"[wifi_bridge] transaction send error: {e}", file=sys.stderr)

    # CMD 0x0F index mapping for the telemetry-mode switch.
    # See TASK/send_data.c::Process_GroundStation_Command (idx 100..102).
    # idx 0..12 is the MRAC feature-flag multiplex (different handler).
    _TELEMETRY_MODE_INDEX = {
        "legacy":          100,   # always emit Frame A/B/C
        "mixed":           101,   # Frame A/B/C + subscribe (boot default)
        "subscribe_only":  102,   # skip legacy path; Send_Task at 200 Hz
    }

    def set_telemetry_mode_now(self, mode: str) -> None:
        """Fire-and-forget FC telemetry-mode switch via CMD 0x0F.

        Sends the 9-byte command frame directly over the WiFi socket (NOT
        through the cmd queue). The FC's USART3 IRQ handler sets
        ``g_telemetry_mode`` and the next ``Send_Task`` cycle picks up the
        new cadence. Must be called AFTER ``start()`` because the wifi
        socket is created there.

        Args:
            mode: ``"legacy"``, ``"mixed"``, or ``"subscribe_only"``.

        Caveats (2026-09-09 subscribe-only-bug):
            * ``"subscribe_only"`` skips the legacy Frame A/B/C path so
              ``Send_Task`` runs at the nominal 200 Hz cadence instead of
              being paced to ~80 Hz by the UART4 DMA busy-wait. BUT the
              current firmware does NOT process 0x21 subscribe control-plane
              requests in SUBSCRIBE_ONLY mode — 0x08 schema replies never
              arrive. Until ``USER/main.c`` is refactored to extract the
              control-plane handler out of ``Send_Groundstation_Telemetry_UART4``,
              SUBSCRIBE_ONLY mode breaks new subscribe requests. Wire the
              subscribe BEFORE switching to SUBSCRIBE_ONLY if you need to
              (e.g. ``--no-auto-subscribe`` plus manual subscribe_preset
              calls, then poke ``g_telemetry_mode`` to 2 via livewatch).
        """
        if mode not in self._TELEMETRY_MODE_INDEX:
            raise ValueError(
                f"mode must be one of {sorted(self._TELEMETRY_MODE_INDEX)}, "
                f"got {mode!r}"
            )
        if self._wifi is None:
            raise RuntimeError(
                "wifi socket not initialised; call start() before "
                "set_telemetry_mode_now()"
            )
        idx = self._TELEMETRY_MODE_INDEX[mode]
        # Value is ignored for idx 100..102 (the FC only reads the index).
        self._send_cmd_frame(0x0F, idx, 0.0)
        print(f"[wifi_bridge] Sent telemetry-mode switch: {mode} (CMD 0x0F idx={idx})",
              flush=True)


    # ------------------------------------------------------------------
    # Subscribe-stream decode + VoFA+ forwarding
    # ------------------------------------------------------------------

    def _handle_schema_frame(self, frame: bytes) -> Optional[int]:
        """Decode and register a 0x08 schema reply.

        Returns the schema's slot index on success (so the caller can wake
        up a waiting subscribe_preset() call), or None if the frame was
        malformed and the schema was not registered.

        Firmware layout (Subscribe_BuildSchema, API/subscribe.c):
          [0xAA][0xBB][0x08][LEN_HI][LEN_LO][n_ranges]
          [divider][transport][slot][total_hi][total_lo]
          [ranges...][XOR CRC]
          payload_len = 5 + n_ranges*8 (range block starts at frame[11]).

        Each range:
          [address uint32 LE][size uint16 LE][count uint16 LE]
        """
        try:
            from ground_station.livewatch.stream import StreamRange, StreamSchema

            payload_len = (frame[3] << 8) | frame[4]
            if len(frame) != 6 + payload_len + 1:
                return None

            payload = frame[6:6 + payload_len]
            if len(payload) < 5:
                return None

            n_ranges = frame[5]
            divider = payload[0]
            transport = payload[1]
            slot = payload[2]
            total_bytes = (payload[3] << 8) | payload[4]

            # S15: re-attach DWARF names from the pending 0x21 request.
            # The wire protocol does NOT carry names in the 0x08 reply —
            # only address/size/count — so without this lookup the schema's
            # ranges come back anonymous and the dashboard falls back to
            # ``ch{slot}.{idx}`` for every variable. See S15-audit.md §1.
            with self._stream_lock:
                requested = self._pending_schema_ranges.get(slot)

            # Build a (address, size) -> StreamRange lookup from the
            # original request so we can match by (address, size).
            by_addr: Dict[tuple, StreamRange] = {}
            if requested is not None:
                for r in requested:
                    by_addr[(r.address, r.size)] = r

            ranges = []
            offset = 5
            for _ in range(n_ranges):
                if offset + 8 > len(payload):
                    break
                address = struct.unpack_from("<I", payload, offset)[0]
                size = struct.unpack_from("<H", payload, offset + 4)[0]
                count = struct.unpack_from("<H", payload, offset + 6)[0]
                hint = by_addr.get((address, size))
                if hint is not None:
                    # Carry the name (and fmt if the original request had
                    # one) through to the schema range so the decoder can
                    # label the channels.
                    ranges.append(StreamRange(
                        address=address, size=size, count=count,
                        name=hint.name, fmt=hint.fmt,
                    ))
                else:
                    ranges.append(StreamRange(
                        address=address, size=size, count=count,
                    ))
                offset += 8

            if total_bytes != sum(r.nbytes for r in ranges):
                print("[wifi_bridge] Warning: schema total_bytes mismatch",
                      flush=True)
                return None

            schema = StreamSchema(
                divider=divider,
                transport=transport,
                total_bytes=total_bytes,
                ranges=tuple(ranges),
                slot=slot
            )

            with self._stream_lock:
                self._stream_schemas[slot] = schema
                self._pending_schema_ranges.pop(slot, None)
                # Advance the per-slot lifecycle only forward. A schema
                # reply to a fresh request moves 'sent' -> 'schema_received';
                # it never regresses a slot that is already streaming.
                if self._slot_states.get(slot) == "sent":
                    self._slot_states[slot] = "schema_received"

            # S15 instrumentation: log the parsed 0x08 reply so the
            # operator can verify that the schema carries the DWARF
            # names from the request. If a name comes back empty after
            # this line, the request and reply addresses did not match.
            n_named = sum(1 for r in ranges if r.name)
            n_unnamed = len(ranges) - n_named
            print(
                f"[wifi_bridge] Registered schema for slot {slot}: "
                f"{len(ranges)} ranges ({n_named} named, {n_unnamed} unnamed), "
                f"{total_bytes} bytes, divider={divider}",
                flush=True,
            )
            if n_named != len(ranges):
                print(
                    f"[wifi_bridge] [S15] Schema ranges:\n" + "\n".join(
                        f"    0x{r.address:08X} size={r.size} count={r.count} "
                        f"name={r.name!r}" + ("  <-- UNNAMED" if not r.name else "")
                        for r in ranges
                    ),
                    flush=True,
                )
            return slot

        except Exception as e:
            print(f"[wifi_bridge] Warning: failed to decode 0x08 schema: {e}",
                  file=sys.stderr, flush=True)
            return None

    def _decode_stream_frame(self, slot: int, frame: bytes) -> Optional[Dict[str, Any]]:
        """Decode a 0x09+slot subscribe data frame.

        Layout (firmware `Subscribe_BuildStreamFrame`):
          [0xAA][0xBB][TYPE][LEN_HI][LEN_LO][SEQ][T_MS uint32 LE][values...][CRC16 BE]

        The values are packed in range order; within a range, in address
        order (see `livewatch.stream.StreamRange.decode`). Without a
        registered schema for `slot` we emit sequential channel numbers.
        """
        try:
            # payload_len = LEN_HI<<8 | LEN_LO. header is 10 bytes (AA BB TYPE
            # LEN_HI LEN_LO SEQ T_MS_LE_4), payload is T_MS+values, then 2-byte CRC.
            payload_len = (frame[3] << 8) | frame[4]
            if len(frame) != 6 + payload_len + 2:
                return None
            if payload_len < 4 + 2:
                return None  # not enough for [T_MS_LE_4][CRC]
            values_len = payload_len - 4

            # CRC16-CCITT over [TYPE..last value byte]; bad CRC frames are
            # counted and dropped before any value unpacking.
            from ground_station.livewatch.transport import crc16_ccitt
            expected_crc = (frame[-2] << 8) | frame[-1]
            if crc16_ccitt(frame[2:-2]) != expected_crc:
                with self._stream_lock:
                    stats = self._stream_stats.setdefault(
                        slot, {"received": 0, "dropped": 0,
                                "crc_errors": 0, "last_seq": -1})
                    stats["crc_errors"] += 1
                return None

            seq = frame[5]
            t_ms = struct.unpack_from("<I", frame, 6)[0]

            schema = None
            with self._stream_lock:
                schema = self._stream_schemas.get(slot)
            
            values_list = []
            if schema is not None and hasattr(schema, "ranges"):
                offset = 10
                _SIZE_FMT = {1: "b", 2: "h", 4: "f", 8: "d"}
                for rng in schema.ranges:
                    code = rng.fmt or _SIZE_FMT.get(rng.size, "f")
                    fmt_str = f"<{rng.count}{code}"
                    try:
                        unpacked = struct.unpack_from(fmt_str, frame, offset)
                        values_list.extend(unpacked)
                    except struct.error:
                        pass
                    offset += struct.calcsize(fmt_str)
                values = tuple(values_list)
            else:
                n_floats = values_len // 4
                values = struct.unpack_from(f"<{n_floats}f", frame, 10) if n_floats > 0 else ()
        except (struct.error, IndexError):
            return None

        names: list[str] = []
        if schema is not None and hasattr(schema, "ranges"):
            # Build a (channel_index -> name) list from the schema's ranges.
            # StreamRange has address, size, count, name (packed multi-name),
            # and fmt. For a packed range we expand the name list per count.
            idx = 0
            for rng in schema.ranges:
                for i in range(rng.count):
                    if rng.name and "," in rng.name:
                        # Multi-name packed range -- pick the i-th name.
                        # The resolver packed adjacent same-size scalars into
                        # one range in address order, so split by ", ".
                        names_at_idx = [n.strip() for n in rng.name.split(",")]
                        if i < len(names_at_idx):
                            names.append(names_at_idx[i])
                        else:
                            names.append(f"{rng.name}[{i}]")
                    elif rng.name:
                        names.append(rng.name if rng.count == 1 else f"{rng.name}[{i}]")
                    else:
                        names.append(f"ch{slot}.{idx}")
                    idx += 1
        # Pad to match value count if schema is short (defensive).
        while len(names) < len(values):
            names.append(f"ch{slot}.{len(names)}")

        # S15 instrumentation: log the decoded 0x09 frame channel names
        # so the operator can spot when a slot falls back to ``chN.M``
        # instead of using DWARF names. Set the env var
        # GROUND_STATION_DEBUG_NAMES=1 to enable (default: one-line
        # summary only).
        n_named = sum(1 for n in names if not (n.startswith("ch") and "." in n))
        if n_named != len(names):
            print(
                f"[wifi_bridge] [S15] slot {slot} decoded {len(values)} channels "
                f"({n_named} named, {len(values) - n_named} positional fallback)",
                flush=True,
            )

        json_payload = {f"slot{slot}.{name}": round(float(v), 6)
                        for name, v in zip(names, values)}
        with self._stream_lock:
            stats = self._stream_stats.setdefault(
                slot, {"received": 0, "dropped": 0, "crc_errors": 0, "last_seq": -1})
            previous = stats["last_seq"]
            if previous >= 0:
                stats["dropped"] += (seq - previous - 1) & 0xFF
            stats["last_seq"] = int(seq)
            stats["received"] += 1
            stats["bytes_received"] = stats.get("bytes_received", 0) + len(frame)
            received = stats["received"]
            dropped = stats["dropped"]
            crc_errors = stats["crc_errors"]
            bytes_received = stats["bytes_received"]
            # First data frame after acked: slot is live. Data only ever
            # advances the lifecycle forward -- never regress.
            self._slot_states[slot] = "streaming"
        total = received + dropped
        json_payload[f"slot{slot}.t_ms"] = int(t_ms)
        json_payload[f"slot{slot}.seq"] = int(seq)
        json_payload[f"slot{slot}.received"] = received
        json_payload[f"slot{slot}.dropped"] = dropped
        json_payload[f"slot{slot}.crc_errors"] = crc_errors
        # Cumulative actual wire bytes received for this slot (headers +
        # payload + CRC of every 0x09+slot frame). Feeds the live link-
        # budget readout (used B/s vs ~91 304 B/s capacity).
        json_payload[f"slot{slot}.bytes_received"] = bytes_received
        json_payload[f"slot{slot}.loss_pct"] = round(100.0 * dropped / total, 3) if total else 0.0
        return {"json": json_payload, "values": list(values), "names": names}

    def _stream_metadata(self, slot: int):
        """Return a typed ``StreamMetadata`` for ``slot`` from its stats.

        Used by the service seam (``adapt_from_bridge`` / diagnostics) so a
        slot's cumulative loss / CRC-error counters survive as typed metadata
        rather than being folded back into the values dict.
        """
        from ground_station.service.telemetry_adapter import StreamMetadata

        with self._stream_lock:
            s = self._stream_stats.get(slot)
            if not s:
                return StreamMetadata()
            received = s.get("received", 0)
            dropped = s.get("dropped", 0)
            total = received + dropped
            return StreamMetadata(
                received=received,
                dropped=dropped,
                loss_pct=round(100.0 * dropped / total, 3) if total else 0.0,
                crc_errors=s.get("crc_errors", 0),
            )

    def _forward_vofa(self, slot: int, values: list) -> None:
        """Forward one subscribe frame's values to the VoFA+ UDP sink.

        Slot 1 -> UDP 1347 (Frame A). Slot 2 -> UDP 1348 (Frame B). Other
        slots are dropped silently -- the manifest layout reserves slot 1
        and slot 2 for VoFA+ and uses slot 3+ for non-VoFA+ data.
        """
        if not self._vofa_enabled or not values:
            return
        if slot == 1:
            dest_port = self._vofa_port_a
        elif slot == 2:
            dest_port = self._vofa_port_b
        else:
            return
        try:
            payload = struct.pack(f"<{len(values)}f", *values) + b"\x00\x00\x80\x7f"
            self._udp_send.sendto(payload, (self._vofa_host, dest_port))
        except OSError:
            pass


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1].strip())
    cfg = load_config()
    ap.add_argument(
        "--wifi-host", default=str(cfg.get("wifi_bridge_host", "192.168.4.1")),
        help="MicoAir module IP address")
    ap.add_argument(
        "--wifi-port", type=int, default=int(cfg.get("wifi_bridge_wifi_port", 14550)),
        help="MicoAir module UDP port")
    ap.add_argument(
        "--cmd-port", type=int, default=int(cfg.get("wifi_bridge_cmd_port", 1349)),
        help="UDP port the bridge listens for dashboard JSON commands "
             "(default: 1349, matches the dashboard's auto-connect ping)")
    ap.add_argument(
        "--telem-port", type=int, default=int(cfg.get("wifi_bridge_telem_port", 1350)),
        help="UDP port the bridge sends JSON telemetry to (default: 1350)")
    ap.add_argument(
        "--no-vofa", action="store_true",
        help="Disable VoFA+ forwarding (slot 1 -> UDP 1347, slot 2 -> UDP 1348)")
    ap.add_argument(
        "--vofa-host", default=str(cfg.get("vofa_host", "127.0.0.1")),
        help="VoFA+ UDP sink host (default: 127.0.0.1)")
    ap.add_argument(
        "--vofa-port-a", type=int, default=int(cfg.get("vofa_port_a", 1347)),
        help="VoFA+ UDP port for Frame A / slot 1 (default: 1347)")
    ap.add_argument(
        "--vofa-port-b", type=int, default=int(cfg.get("vofa_port_b", 1348)),
        help="VoFA+ UDP port for Frame B / slot 2 (default: 1348)")
    ap.add_argument(
        "--no-auto-subscribe", action="store_true",
        help="Disable auto-subscribe for boot-default telemetry (slot 0). "
             "Use this when the FC sends legacy Frame A/B instead of slot data.")
    ap.add_argument(
        "--mode", choices=("burst", "single"),
        default=cfg.get("wifi_bridge_mode", "burst"),
        help="Preset-subscription mode. 'burst' (default for telemetry-heavy "
             "sessions) aggregates multiple presets into one slot when the "
             "combined payload fits STREAM_MAX_BYTES, falling back to a "
             "per-preset multi-slot allocation when it does not. 'single' "
             "is the legacy one-preset-per-slot behaviour. Equivalent for "
             "single-preset subscriptions. Overrides config.yaml's "
             "wifi_bridge_mode setting.")
    ap.add_argument(
        "--set-telemetry-mode",
        choices=("legacy", "mixed", "subscribe_only"),
        default=None,
        help="One-shot FC telemetry-mode switch via CMD 0x0F idx 100..102. "
             "Fires before the bridge starts streaming so the FC's "
             "telemetry rate settles on the first data frame. "
             "'subscribe_only' (idx 102) skips the legacy Frame A/B/C path "
             "so Send_Task runs at the nominal 200 Hz cadence instead of "
             "being paced to ~80 Hz by the UART4 DMA busy-wait. Default "
             "(no flag) leaves the FC's boot-default mode untouched. "
             "CAUTION: subscribe requests (0x21) do NOT get 0x08 schema "
             "replies in SUBSCRIBE_ONLY mode on the current firmware "
             "(sessions_summary/2026-09-09-subscribe-only-bug.md) — wire "
             "all subscriptions BEFORE switching, or use MIXED for now.")
    args = ap.parse_args(argv)

    print("WiFi bridge starting:")
    print(f"  WiFi:  {args.wifi_host}:{args.wifi_port}")
    print(f"  CMD:   localhost:{args.cmd_port}")
    print(f"  TELEM: localhost:{args.telem_port}")
    if not args.no_vofa:
        print(f"  VoFA+: {args.vofa_host}:{args.vofa_port_a} (slot 1) + "
              f"{args.vofa_port_b} (slot 2)")
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()

    bridge = WifiBridge(
        wifi_host=args.wifi_host,
        wifi_port=args.wifi_port,
        cmd_udp_port=args.cmd_port,
        telem_udp_port=args.telem_port,
        vofa_enabled=not args.no_vofa,
        vofa_host=args.vofa_host,
        vofa_port_a=args.vofa_port_a,
        vofa_port_b=args.vofa_port_b,
        mode=args.mode,
    )
    print(f"  Mode:  {args.mode} ({'aggregate presets into one slot' if args.mode == 'burst' else 'one preset per slot'})")
    if args.set_telemetry_mode is not None:
        print(f"  FC telemetry-mode switch: {args.set_telemetry_mode} (CMD 0x0F before auto-subscribe)")
    try:
        bridge.start(
            auto_subscribe_boot_default=not args.no_auto_subscribe,
            initial_telemetry_mode=args.set_telemetry_mode,
        )
    except OSError as exc:
        # Most common cause: an orphan bridge from a previous session is still
        # holding 1349 / 14550. Surface the port number so the user can find
        # and kill it with `Get-NetUDPEndpoint -LocalPort <n>`.
        print(f"\nERROR: WiFi bridge failed to start: {exc}", file=sys.stderr)
        print("Most likely an orphan bridge from a previous session is still",
              file=sys.stderr)
        print("holding the port. Find it with:", file=sys.stderr)
        print(f"  Get-NetUDPEndpoint -LocalPort {args.cmd_port} -ErrorAction SilentlyContinue",
              file=sys.stderr)
        print(f"  Get-NetUDPEndpoint -LocalPort {args.wifi_port} -ErrorAction SilentlyContinue",
              file=sys.stderr)
        print("Then `Stop-Process -Id <pid> -Force`.", file=sys.stderr)
        return 2

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
