"""RTOS observability bridge  --  reads firmware scheduler metrics over SWD.

The drone's FreeRTOS instance exposes a handful of counters under
``firmware/rtos_observability.c`` plus one owned by ``BSP/usart3.c``:

    platform_obs_send_ticks       u32  monotonic Send_Task cycle counter
    platform_obs_queue_depth      u16  USART3 TX ring depth, bytes
    platform_obs_dma_busy         u16  UART DMA busy flag at sample time
    platform_obs_usart3_tx_drops  u32  mirror of UA3TxDrops (ring-full drops)
    platform_obs_cmd_queue_depth  u16  ground-station command ring depth
    platform_obs_cmd_queue_max    u16  ground-station command ring capacity
    platform_obs_heap_free_bytes  u32  xPortGetFreeHeapSize() snapshot
    UA3TxFrames                   u32  USART3 frames accepted into the TX ring
    xTickCount                    u32  FreeRTOS monotonic tick counter (ms)

These nine symbols live at known addresses in the firmware ELF (widths are
DWARF-resolved by ``LiveReader`` -- u16 vs u32 must not be guessed). Only
``xTickCount`` reaches the Wi-Fi telemetry stream; the rest are SWD-only.
This bridge reads them periodically over the wireless CMSIS-DAP link and
injects them as ``streams["rtos"]`` so ``resource-panel.js`` and the
bandwidth manager see them.

Activation
~~~~~~~~~~
The bridge is OFF by default (CI runs without an SWD probe). Enable on
the launcher with::

    python -m ground_station.service --rtos-bridge --rtos-interval 5

The launcher spawns a daemon thread that:

* builds a coalesced read plan over the nine symbols via ``LiveReader``
  (they merge into two SWD transactions thanks to adjacency in RAM),
* connects to the wireless CMSIS-DAP transport,
* samples at ``--rtos-interval`` Hz (default 5),
* publishes each sample via ``GroundStationService.inject_external_stream``
  under slot ``"rtos"`` (string key  --  distinct from integer Wi-Fi slots).

Failure mode: ``LiveReader`` raises ``LiveTransportError`` if no probe is
attached. The bridge logs once and shuts down its thread silently so
the rest of the service stays alive.

Limits
~~~~~~
The platform/ folder spec originally capped this module at ~150 lines; the
S15 observability widening (nine symbols, explicit key mapping) pushes it
somewhat above that on purpose. Any further feature creep belongs in a
follow-up session.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ground_station.service.core import GroundStationService

LOG = logging.getLogger("ground_station.platform.rtos_bridge")


# Symbol names to read. Verified against OBJ/JX_FLY.axf symbol table
# 2026-09-21 (addresses/sizes: see ELF; widths come from DWARF at runtime).
RTOS_SYMBOLS: tuple[str, ...] = (
    "platform_obs_send_ticks",
    "platform_obs_queue_depth",
    "platform_obs_dma_busy",
    "platform_obs_usart3_tx_drops",
    "platform_obs_cmd_queue_depth",
    "platform_obs_cmd_queue_max",
    "platform_obs_heap_free_bytes",
    "UA3TxFrames",
    "xTickCount",
)

# Slot key used for the injected stream. String (not int) so it can never
# collide with the Wi-Fi integer slots (0..3). ``resource-panel.js`` looks
# for ``state.streams['rtos']``.
RTOS_SLOT_KEY: str = "rtos"


@dataclass(frozen=True)
class RtosSample:
    """One decoded RTOS snapshot, ready to inject into the service state."""

    send_task_ticks: int
    queue_depth: int
    dma_busy: int
    usart3_tx_drops: int
    cmd_queue_depth: int
    cmd_queue_max: int
    heap_free_bytes: int
    usart3_tx_count: int  # frames, not bytes (source: UA3TxFrames)
    scheduler_tick_count: int

    def as_values(self) -> dict[str, float]:
        """Flat dict in the shape ``resource-panel.js`` expects.

        ``rtos.usart3_tx_bytes`` is deliberately absent: no cumulative byte
        counter exists in this firmware build.
        """
        return {
            "rtos.send_task_ticks":       float(self.send_task_ticks),
            "rtos.queue_depth":           float(self.queue_depth),
            "rtos.dma_busy":              float(self.dma_busy),
            "rtos.usart3_tx_drops":       float(self.usart3_tx_drops),
            "rtos.cmd_queue_depth":       float(self.cmd_queue_depth),
            "rtos.cmd_queue_max":         float(self.cmd_queue_max),
            "rtos.heap_free_bytes":       float(self.heap_free_bytes),
            "rtos.usart3_tx_count":       float(self.usart3_tx_count),
            "rtos.scheduler_tick_count":  float(self.scheduler_tick_count),
        }


class RtosBridge:
    """Background SWD reader that injects RTOS metrics into the service state.

    Designed to be a no-op when no probe is attached: any transport error
    on the first ``sample()`` call is logged once and the thread exits
    cleanly. Callers can safely construct and ``start()`` the bridge on
    every launch.

    Attributes
    ----------
    interval_hz : float
        Polling cadence. Default 5 Hz. Lower than the Wi-Fi slot rate so
        we don't starve the SWD link.
    sequence : int
        Monotonic sample counter  --  used as the bridge's external sequence
        number, surfaces in ``streams['rtos'].sequence``.
    """

    DEFAULT_INTERVAL_HZ: float = 5.0

    def __init__(self, service: "GroundStationService",
                 elf_path: str | Path,
                 interval_hz: float = DEFAULT_INTERVAL_HZ) -> None:
        self._service = service
        self._elf_path = Path(elf_path)
        self.interval_hz = float(interval_hz)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sequence: int = 0

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Spawn the background poller. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="rtos_bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the poller to exit and wait for the thread to join."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ---- internal --------------------------------------------------------

    def _run(self) -> None:
        """Background loop. Builds a read plan once, samples forever."""
        try:
            from ground_station.livewatch.reader import LiveReader
        except ImportError as exc:
            LOG.warning("LiveReader not importable (%s); RTOS bridge disabled", exc)
            return
        try:
            reader = LiveReader(self._elf_path)
            reader.connect()
        except Exception as exc:
            # Most common cause: no SWD probe attached. CI runs do not
            # have a wireless debugger; the bridge must exit silently.
            LOG.info("RTOS bridge disabled: %s", exc)
            return

        try:
            plan = reader.plan(list(RTOS_SYMBOLS))
        except Exception as exc:
            LOG.warning("RTOS bridge: could not build plan for %s: %s",
                        RTOS_SYMBOLS, exc)
            reader.close()
            return

        period = 1.0 / max(0.1, self.interval_hz)
        LOG.info("RTOS bridge started: %d symbols at %.1f Hz",
                 len(RTOS_SYMBOLS), self.interval_hz)
        try:
            while not self._stop.is_set():
                try:
                    sample = self._read_one(reader, plan)
                    if sample is not None:
                        self.sequence += 1
                        self._service.inject_external_stream(
                            RTOS_SLOT_KEY,
                            sample.as_values(),
                            sequence=self.sequence,
                        )
                except Exception as exc:
                    # One bad sample should not kill the bridge. Log at
                    # debug so a flapping link is visible but not noisy.
                    LOG.debug("RTOS bridge sample failed: %s", exc)
                # Sleep is interruptible by ``stop()``.
                if self._stop.wait(timeout=period):
                    break
        finally:
            reader.close()
            LOG.info("RTOS bridge stopped after %d samples", self.sequence)

    def _read_one(self, reader, plan) -> RtosSample | None:
        """Read the nine symbols and pack into an :class:`RtosSample`.

        Returns ``None`` when the sample cannot decode (e.g. DMA underrun
        during a long busy-wait on the firmware side).
        """
        decoded: dict[str, Any] = reader.sample(plan)
        # The reader's decode() yields either a scalar (float/int) or
        # bytes (for an aggregate we did not request). All nine RTOS
        # symbols are scalars.
        try:
            return RtosSample(
                send_task_ticks=int(decoded["platform_obs_send_ticks"]),
                queue_depth=int(decoded["platform_obs_queue_depth"]),
                dma_busy=int(decoded["platform_obs_dma_busy"]),
                usart3_tx_drops=int(decoded["platform_obs_usart3_tx_drops"]),
                cmd_queue_depth=int(decoded["platform_obs_cmd_queue_depth"]),
                cmd_queue_max=int(decoded["platform_obs_cmd_queue_max"]),
                heap_free_bytes=int(decoded["platform_obs_heap_free_bytes"]),
                usart3_tx_count=int(decoded["UA3TxFrames"]),
                scheduler_tick_count=int(decoded["xTickCount"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            LOG.debug("RTOS bridge decode miss: %s", exc)
            return None


def build_bridge(service: "GroundStationService",
                 *, elf_path: str | Path | None = None,
                 interval_hz: float = RtosBridge.DEFAULT_INTERVAL_HZ) -> RtosBridge:
    """Convenience factory used by the launcher.

    Resolves ``elf_path`` to ``<repo>/OBJ/JX_FLY.axf`` by default; lets
    callers override via the CLI for development.
    """
    if elf_path is None:
        elf_path = Path(__file__).resolve().parents[2] / "OBJ" / "JX_FLY.axf"
    return RtosBridge(service, elf_path, interval_hz=interval_hz)