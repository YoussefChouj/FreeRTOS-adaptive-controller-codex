"""Full pyOCD capability layer — all read and write paths exposed.

This module wraps every meaningful pyOCD API. It is NOT restricted to attach/read-only
mode. The drone is a research platform and the operator has explicitly unlocked all tools.

Capabilities:
  - read / write memory (8, 16, 32, 64 bit)
  - core register read / write
  - flash read / write / erase
  - halt / step / resume / reset
  - software and hardware breakpoints
  - watchpoints (read/write/access)
  - RTT streaming (up and down channels)
  - SWO / SWV trace capture
  - gdbserver (TCP port)
  - probe enumeration

pyOCD docs: https://pyocd.io/docs
pyOCD source: https://github.com/pyocd/pyOCD
"""
from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "ProbeSession",      # context manager — owns the pyOCD session lifecycle
    "ProbeError",        # all errors bubble as this
    "CORE_REGISTERS",    # canonical ARM Cortex-M register names
    "WatchpointType",    # WATCHPOINT_READ / WRITE / ACCESS
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ProbeError(RuntimeError):
    """Any pyOCD operation that failed."""


# ---------------------------------------------------------------------------
# pyOCD bootstrap — import lazily so the rest of livewatch stays importable offline
# ---------------------------------------------------------------------------

def _import_pyocd():
    try:
        from pyocd.core.helpers import ConnectHelper
        import pyocd
        return ConnectHelper, pyocd
    except ImportError as exc:
        raise ProbeError(
            "pyocd not installed — run: pip install pyocd"
        ) from exc


# ---------------------------------------------------------------------------
# Core register names (ARM Cortex-M)
# ---------------------------------------------------------------------------

CORE_REGISTERS = [
    # General
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12",
    # Stack pointers
    "msp", "psp", "msplim", "psplim",
    # Program counter / link register
    "pc", "lr",
    # Program status
    "xpsr", "apsr", "iapsr", "eapsr", "psr",
    # CONTROL / FAULTMASK / BASEPRI / PRIMASK
    "control", "faultmask", "basepri", "primask",
    # FPS (if FPU present)
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "s12", "s13", "s14", "s15",
    "s16", "s17", "s18", "s19", "s20", "s21", "s22", "s23",
    "s24", "s25", "s26", "s27", "s28", "s29", "s30", "s31",
    "fpscr",
    # Double-precision
    "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7",
    "d8", "d9", "d10", "d11", "d12", "d13", "d14", "d15",
]


# ---------------------------------------------------------------------------
# Watchpoint type
# ---------------------------------------------------------------------------

class WatchpointType(Enum):
    READ   = "read"
    WRITE  = "write"
    ACCESS = "access"   # read OR write


# ---------------------------------------------------------------------------
# Breakpoint kind
# ---------------------------------------------------------------------------

class BreakpointKind(Enum):
    SW = "sw"   # software (instruction patch)
    HW = "hw"   # hardware (FPB unit)


# ---------------------------------------------------------------------------
# RTT channel direction
# ---------------------------------------------------------------------------

class RTTDirection(Enum):
    UP   = "up"    # target -> host  (terminal output, logs)
    DOWN = "down"  # host -> target  (keystrokes, commands)


# ---------------------------------------------------------------------------
# Main session object
# ---------------------------------------------------------------------------

@dataclass
class RTTCapture:
    channel: int
    data: bytes
    timestamp_s: float


@dataclass
class SWOCapture:
    data: bytes
    timestamp_s: float


@dataclass
class WatchpointEvent:
    address: int
    size: int
    kind: WatchpointType
    value: int | None   # captured value if READ or ACCESS


@dataclass
class BreakpointEvent:
    address: int
    kind: BreakpointKind


class ProbeSession:
    """Full pyOCD session with all capabilities unlocked.

    Usage::

        with ProbeSession() as probe:
            probe.halt()
            regs = probe.registers_read()
            probe.write_memory(0x20000000, 0x12345678)
            probe.resume()

    All methods raise ``ProbeError`` on failure.
    """

    # ----- session lifecycle -----

    def __init__(
        self,
        target: str = "cortex_m",
        connect_mode: str = "attach",
        resume_on_disconnect: bool = False,
        cmsis_dap_limit_packets: bool = True,
        probe_descriptors: str | None = None,
    ):
        """
        Args:
            target: pyOCD target name. Default ``cortex_m`` works for any Cortex-M.
            connect_mode: ``attach`` (halt-on-connect) or ``halt`` (normal attach).
            resume_on_disconnect: If True the core resumes when the session closes.
            cmsis_dap_limit_packets: Force single-in-flight USB for flaky wireless probes.
            probe_descriptors: USB VID/PID, board ID, or unique ID to select a specific probe.
        """
        self.target_name   = target
        self.connect_mode  = connect_mode
        self.resume_on_disconnect = resume_on_disconnect
        self.cmsis_dap_limit_packets = cmsis_dap_limit_packets
        self.probe_descriptors = probe_descriptors
        self._session = None
        self._target  = None
        self._bp_handles: dict[int, object] = {}
        self._wp_handles: dict[int, object] = {}
        self._rtt_channels: dict[int, object] = {}
        self._swo_reader: object | None = None
        self._rtt_server_sock: socket.socket | None = None

    # ---- context manager ----

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        try:
            self.close()
        except Exception:
            pass  # never let close() failures propagate — session must be released
        return False

    def open(self):
        ConnectHelper, pyocd = _import_pyocd()

        options = {
            "target_override": self.target_name,
            "connect_mode":    self.connect_mode,
            "resume_on_disconnect": self.resume_on_disconnect,
        }
        if self.cmsis_dap_limit_packets:
            options["cmsis_dap.limit_packets"] = True

        # Select probe by descriptor if given
        if self.probe_descriptors:
            self._session = ConnectHelper.session_with_chosen_probe(
                options=options,
                return_first=False,
                search=self.probe_descriptors,
            )
        else:
            self._session = ConnectHelper.session_with_chosen_probe(
                options=options,
                return_first=False,
            )

        if self._session is None:
            raise ProbeError(
                "no CMSIS-DAP probe found — is a debugger connected? "
                "Is another session holding it (uVision, OpenOCD, etc.)?"
            )

        try:
            self._session.open()
        except Exception as exc:
            self._session = None
            raise ProbeError(f"failed to open probe session: {exc}") from exc

        self._target = self._session.target
        if self._target is None:
            self._session.close()
            self._session = None
            raise ProbeError(f"target '{self.target_name}' not found on this probe")
        return self

    def close(self):
        if self._swo_reader is not None:
            try:
                self._target.swo.stop()
            except Exception:
                pass
            self._swo_reader = None

        if self._rtt_server_sock is not None:
            try:
                self._rtt_server_sock.close()
            except Exception:
                pass
            self._rtt_server_sock = None

        for handle in list(self._bp_handles.values()):
            try:
                self._target.bp.remove(handle)
            except Exception:
                pass
        self._bp_handles.clear()

        for handle in list(self._wp_handles.values()):
            try:
                self._target.wp.remove(handle)
            except Exception:
                pass
        self._wp_handles.clear()

        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._target = None

    @property
    def target(self):
        """Raw pyOCD target object — for advanced use."""
        if self._target is None:
            raise ProbeError("session not open — use ProbeSession() as a context manager")
        return self._target

    @property
    def elf(self):
        """ELF file loaded by pyOCD, or None."""
        return getattr(self._target, "elf", None)

    # -------------------------------------------------------------------------
    # Memory — read
    # -------------------------------------------------------------------------

    def read_memory(self, addr: int, size: int = 32, phys: bool = False) -> int:
        """Read ``size``-bit memory as an integer.

        Args:
            addr: RAM address.
            size: 8, 16, 32, or 64 bits.
            phys: Unused; kept for backwards-compat with callers. pyOCD
                0.45's MemoryInterface.read*() takes no ``phys`` kwarg.
        Returns:
            Integer value at that address.
        """
        if size == 8:
            v = self._target.read8(addr)
        elif size == 16:
            v = self._target.read16(addr)
        elif size == 32:
            v = self._target.read32(addr)
        elif size == 64:
            v = self._target.read64(addr)
        else:
            raise ValueError(f"size must be 8/16/32/64, got {size}")
        return int(v)

    def read_memory_block8(self, addr: int, size: int, phys: bool = False) -> bytes:
        """Read a contiguous block of bytes from RAM.

        This is the most efficient read — use it for structs, arrays, etc.
        Returns ``size`` bytes.
        """
        del phys  # kept for API compat; pyOCD 0.45 takes no phys kwarg
        try:
            return bytes(self._target.read_memory_block8(addr, size))
        except Exception as exc:
            raise ProbeError(f"read_memory_block8(0x{addr:08X}, {size}) failed: {exc}") from exc

    def read_memory_block32(self, addr: int, count: int) -> list[int]:
        """Read ``count`` 32-bit words as a list of integers."""
        try:
            return list(self._target.read_memory_block32(addr, count))
        except Exception as exc:
            raise ProbeError(f"read_memory_block32(0x{addr:08X}, {count}) failed: {exc}") from exc

    def dump_memory(self, addr: int, size: int) -> dict[str, str]:
        """Return a hexdump-style dict for inspection.

        Returns ``{"hex": "<ascii hex bytes>", "ascii": "<printable chars>"}``.
        """
        raw = self.read_memory_block8(addr, size)
        hex_part = " ".join(f"{b:02X}" for b in raw)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
        return {"hex": hex_part, "ascii": asc_part}

    # -------------------------------------------------------------------------
    # Memory — write
    # -------------------------------------------------------------------------

    def write_memory(self, addr: int, value: int, size: int = 32, phys: bool = False):
        """Write ``size``-bit integer to RAM.

        Args:
            addr: RAM address.
            value: Integer value to write.
            size: 8, 16, 32, or 64 bits.
            phys: Unused; kept for backwards-compat with callers.
        """
        if size == 8:
            self._target.write8(addr, value)
        elif size == 16:
            self._target.write16(addr, value)
        elif size == 32:
            self._target.write32(addr, value)
        elif size == 64:
            self._target.write64(addr, value)
        else:
            raise ValueError(f"size must be 8/16/32/64, got {size}")

    def write_memory_block8(self, addr: int, data: bytes, phys: bool = False):
        """Write a contiguous block of bytes to RAM."""
        del phys  # kept for API compat; pyOCD 0.45 takes no phys kwarg
        try:
            self._target.write_memory_block8(addr, list(data))
        except Exception as exc:
            raise ProbeError(f"write_memory_block8(0x{addr:08X}, {len(data)} B) failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # Flash — read / write / erase
    # -------------------------------------------------------------------------

    def read_flash(self, addr: int, size: int) -> bytes:
        """Read ``size`` bytes directly from flash memory."""
        try:
            return bytes(self._target.read_memory_block8(addr, size))
        except Exception as exc:
            raise ProbeError(f"read_flash(0x{addr:08X}, {size}) failed: {exc}") from exc

    def write_flash(self, addr: int, data: bytes, progress: bool = True):
        """Program flash with ``data`` starting at ``addr``.

        This ERASES the sectors covered by ``data`` first.
        Progress is printed to stdout.
        """
        try:
            self._target.flash.init()
            self._target.flash.erase(addr, addr + len(data))
            self._target.flash.write(addr, data)
            if progress:
                print(f"[probe] flashed {len(data)} B to 0x{addr:08X}")
        except Exception as exc:
            raise ProbeError(f"write_flash(0x{addr:08X}, {len(data)} B) failed: {exc}") from exc

    def flash_elf(self, elf_path: str | Path, progress: bool = True):
        """Flash an entire ELF file to the target.

        Uses pyOCD's built-in ELF loader — handles all sections automatically.
        """
        try:
            self._target.flash.init()
            self._target.flash.loader = self._target.flash.get_loader()
            self._target.flash.loader.flash_file(str(elf_path))
            if progress:
                print(f"[probe] flashed ELF {elf_path}")
        except Exception as exc:
            raise ProbeError(f"flash_elf({elf_path}) failed: {exc}") from exc

    def erase_flash(self, addr: int = 0, size: int | None = None):
        """Erase flash starting at ``addr`` for ``size`` bytes (or all if None)."""
        try:
            self._target.flash.init()
            if size is None:
                self._target.flash.erase_all()
                print("[probe] erased all flash")
            else:
                self._target.flash.erase(addr, addr + size)
                print(f"[probe] erased flash 0x{addr:08X} + {size} B")
        except Exception as exc:
            raise ProbeError(f"erase_flash failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # Core execution control
    # -------------------------------------------------------------------------

    def halt(self):
        """Halt the core immediately."""
        try:
            self._target.halt()
        except Exception as exc:
            raise ProbeError(f"halt failed: {exc}") from exc

    def resume(self):
        """Resume the core from halt."""
        try:
            self._target.resume()
        except Exception as exc:
            raise ProbeError(f"resume failed: {exc}") from exc

    def step(self):
        """Single-step one instruction."""
        try:
            self._target.step()
        except Exception as exc:
            raise ProbeError(f"step failed: {exc}") from exc

    def reset(self, mode: str = "system"):
        """Reset the core.

        Args:
            mode: ``system`` (default), ``core`` (core-only), ``hw`` (hardware assertion).
        """
        try:
            from pyocd.core.target import Target
            reset_types = {
                "system": Target.ResetType.SYSRESETREQ,
                "core": Target.ResetType.CORE,
                "hw": Target.ResetType.HARDWARE,
            }
            if mode not in reset_types:
                raise ValueError(f"unknown reset mode {mode!r}")
            self._target.reset(reset_types[mode])
        except Exception as exc:
            raise ProbeError(f"reset({mode}) failed: {exc}") from exc

    def reset_halt(self):
        """Reset and halt immediately after reset."""
        try:
            from pyocd.core.target import Target
            self._target.reset(Target.ResetType.SYSRESETREQ)
            self._target.halt()
        except Exception as exc:
            raise ProbeError(f"reset_halt failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # Core registers
    # -------------------------------------------------------------------------

    def register_read(self, name: str) -> int:
        """Read a named core register.

        Common names: ``r0``–``r12``, ``msp``, ``psp``, ``pc``, ``lr``,
        ``xpsr``, ``control``, ``primask``, ``basepri``, ``faultmask``.
        """
        try:
            return int(self._target.read_core_register(name))
        except Exception as exc:
            raise ProbeError(f"read_core_register({name!r}) failed: {exc}") from exc

    def register_write(self, name: str, value: int):
        """Write a named core register."""
        try:
            self._target.write_core_register(name, value)
        except Exception as exc:
            raise ProbeError(f"write_core_register({name!r}, 0x{value:X}) failed: {exc}") from exc

    def registers_read(self) -> dict[str, int]:
        """Read all standard ARM Cortex-M core registers."""
        out = {}
        for name in CORE_REGISTERS:
            try:
                out[name] = int(self._target.read_core_register(name))
            except Exception:
                pass   # register not present (e.g. FPU regs on M0)
        return out

    def registers_dump(self) -> str:
        """Readable dump of all readable core registers."""
        regs = self.registers_read()
        lines = []
        for name, val in regs.items():
            lines.append(f"  {name:>8s}  = 0x{val:08X}  ({val})")
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Breakpoints
    # -------------------------------------------------------------------------

    def bp_set(self, addr: int, kind: BreakpointKind = BreakpointKind.HW) -> int:
        """Set a breakpoint at ``addr``.

        Returns the breakpoint handle. Raise on failure.
        """
        bp_type = {"sw": "sw", "hw": "hw"}.get(kind.value, "hw")
        try:
            handle = self._target.bp.set(addr, type=bp_type)
            self._bp_handles[addr] = handle
            print(f"[probe] bp set  0x{addr:08X} ({kind.value})")
            return handle
        except Exception as exc:
            raise ProbeError(f"bp.set(0x{addr:08X}) failed: {exc}") from exc

    def bp_remove(self, addr: int):
        """Remove breakpoint at ``addr``."""
        handle = self._bp_handles.pop(addr, None)
        if handle is None:
            raise ProbeError(f"no breakpoint found at 0x{addr:08X}")
        try:
            self._target.bp.remove(handle)
            print(f"[probe] bp removed 0x{addr:08X}")
        except Exception as exc:
            raise ProbeError(f"bp.remove(0x{addr:08X}) failed: {exc}") from exc

    def bp_list(self) -> list[dict]:
        """List all active breakpoints."""
        out = []
        for addr, handle in self._bp_handles.items():
            out.append({"addr": addr, "handle": handle})
        return out

    def bp_remove_all(self):
        """Remove all breakpoints."""
        for addr in list(self._bp_handles.keys()):
            self.bp_remove(addr)

    # -------------------------------------------------------------------------
    # Watchpoints
    # -------------------------------------------------------------------------

    def wp_set(
        self,
        addr: int,
        size: int = 4,
        kind: WatchpointType = WatchpointType.ACCESS,
    ) -> object:
        """Set a watchpoint on a memory region.

        Args:
            addr: Start address.
            size: Size in bytes to watch (1, 2, 4, 8).
            kind: READ, WRITE, or ACCESS (either).
        Returns the watchpoint handle.
        """
        # pyOCD wp.set takes addr, size, and type as string
        try:
            handle = self._target.wp.set(
                addr=addr,
                size=size,
                type=kind.value,
            )
            self._wp_handles[addr] = handle
            print(f"[probe] wp set  0x{addr:08X} +{size} B ({kind.value})")
            return handle
        except Exception as exc:
            raise ProbeError(f"wp.set(0x{addr:08X}, {size}) failed: {exc}") from exc

    def wp_remove(self, addr: int):
        """Remove watchpoint at ``addr``."""
        handle = self._wp_handles.pop(addr, None)
        if handle is None:
            raise ProbeError(f"no watchpoint found at 0x{addr:08X}")
        try:
            self._target.wp.remove(handle)
            print(f"[probe] wp removed 0x{addr:08X}")
        except Exception as exc:
            raise ProbeError(f"wp.remove(0x{addr:08X}) failed: {exc}") from exc

    def wp_remove_all(self):
        """Remove all watchpoints."""
        for addr in list(self._wp_handles.keys()):
            self.wp_remove(addr)

    # -------------------------------------------------------------------------
    # RTT — Real Time Transfer
    # -------------------------------------------------------------------------

    def rtt_start(self):
        """Start RTT polling."""
        try:
            self._target.rtt.start()
            print("[probe] RTT started")
        except Exception as exc:
            raise ProbeError(f"rtt.start() failed: {exc}") from exc

    def rtt_stop(self):
        """Stop RTT polling."""
        try:
            self._target.rtt.stop()
            self._rtt_channels.clear()
            print("[probe] RTT stopped")
        except Exception as exc:
            raise ProbeError(f"rtt.stop() failed: {exc}") from exc

    def rtt_read_channel(self, channel: int = 0, timeout_s: float = 1.0) -> list[RTTCapture]:
        """Read all available data from an RTT up-channel (target -> host).

        Args:
            channel: RTT channel number (default 0 = terminal).
            timeout_s: How long to wait for data.
        Returns:
            List of RTTCapture objects with ``data`` as bytes.
        """
        try:
            self._target.rtt.start()
        except Exception:
            pass   # already started

        out = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data = self._target.rtt.read(channel)
                if data:
                    out.append(RTTCapture(
                        channel=channel,
                        data=bytes(data),
                        timestamp_s=time.monotonic(),
                    ))
                else:
                    break
            except Exception:
                break
        return out

    def rtt_write_channel(self, channel: int, data: bytes | str):
        """Write to an RTT down-channel (host -> target).

        Args:
            channel: RTT channel number.
            data: Bytes or string to send.
        """
        if isinstance(data, str):
            data = data.encode()
        try:
            self._target.rtt.start()
        except Exception:
            pass
        try:
            self._target.rtt.write(channel, data)
        except Exception as exc:
            raise ProbeError(f"rtt.write(channel={channel}) failed: {exc}") from exc

    def rtt_list_channels(self) -> dict[str, list[dict]]:
        """Return RTT channel info: ``{"up": [...], "down": [...]}``."""
        try:
            self._target.rtt.start()
        except Exception:
            pass
        try:
            info = self._target.rtt.get_control_block()
        except Exception as exc:
            raise ProbeError(f"rtt.get_control_block() failed: {exc}") from exc

        if info is None:
            return {"up": [], "down": []}

        def _chan_list(channels):
            return [
                {"index": i, "name": getattr(c, "name", str(i)),
                 "buf_size": getattr(c, "size", 0)}
                for i, c in enumerate(channels)
            ]

        return {
            "up":   _chan_list(getattr(info, "up_channels", [])),
            "down": _chan_list(getattr(info, "down_channels", [])),
        }

    def rtt_stream(self, channel: int = 0, duration_s: float | None = None,
                   on_data=None) -> Iterator[bytes]:
        """Yield RTT data chunks from an up-channel until ``duration_s`` or until
        the session closes.

        Args:
            channel: RTT channel to read.
            duration_s: Stream for this many seconds, or None for indefinite.
            on_data: Optional callback ``(bytes) -> None`` called for each chunk.
        """
        try:
            self._target.rtt.start()
        except Exception:
            pass
        t0 = time.monotonic()
        while True:
            try:
                data = self._target.rtt.read(channel)
                if data:
                    chunk = bytes(data)
                    if on_data:
                        on_data(chunk)
                    yield chunk
            except Exception:
                pass
            if duration_s and (time.monotonic() - t0) >= duration_s:
                return
            time.sleep(0.01)

    def rtt_telnet(self, port: int = 20294):
        """Start a telnet server that bridges RTT channel 0.

        Connect with ``telnet localhost <port>`` to interact with the target's
        RTT up/down channels.
        """
        try:
            self._target.rtt.start()
        except Exception:
            pass
        try:
            self._target.rtt.start_telnet(port=port)
            print(f"[probe] RTT telnet server on port {port}")
        except Exception as exc:
            raise ProbeError(f"rtt.start_telnet({port}) failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # SWO / SWV trace
    # -------------------------------------------------------------------------

    def swo_start(self, baud: int = 1000000):
        """Start SWO trace capture at ``baud`` bits/s.

        Requires the target to have ITM and SWO pin configured.
        """
        try:
            self._target.swo.start(baud=baud)
            print(f"[probe] SWO trace started at {baud} baud")
        except Exception as exc:
            raise ProbeError(f"swo.start(baud={baud}) failed: {exc}") from exc

    def swo_stop(self):
        """Stop SWO trace capture."""
        try:
            self._target.swo.stop()
            self._swo_reader = None
            print("[probe] SWO trace stopped")
        except Exception as exc:
            raise ProbeError(f"swo.stop() failed: {exc}") from exc

    def swo_read(self, timeout_s: float = 1.0) -> list[SWOCapture]:
        """Read accumulated SWO data."""
        out = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data = self._target.swo.read()
                if data:
                    out.append(SWOCapture(
                        data=bytes(data),
                        timestamp_s=time.monotonic(),
                    ))
                else:
                    break
            except Exception:
                break
        return out

    def swo_stream(self, duration_s: float | None = None) -> Iterator[SWOCapture]:
        """Yield SWO data until ``duration_s`` or session close."""
        try:
            self._target.swo.start()
        except Exception:
            pass
        t0 = time.monotonic()
        while True:
            try:
                data = self._target.swo.read()
                if data:
                    yield SWOCapture(data=bytes(data), timestamp_s=time.monotonic())
            except Exception:
                pass
            if duration_s and (time.monotonic() - t0) >= duration_s:
                return
            time.sleep(0.01)

    # -------------------------------------------------------------------------
    # GDB server
    # -------------------------------------------------------------------------

    def gdbserver_start(self, port: int = 3333, telnet_port: int = 4444):
        """Start a GDB server on the given TCP port.

        Any GDB client can then connect with ``target remote localhost:<port>``.
        The telnet port is for interactive ITM / semihosting access.

        This call BLOCKS. Run in a thread or subprocess for non-blocking use.
        """
        from pyocd.gdbserver import GDBServer
        try:
            server = GDBServer(
                self._target,
                port=port,
                telnet_port=telnet_port,
                persist=True,
            )
            print(f"[probe] GDB server listening on :{port}  (telnet :{telnet_port})")
            server.run()
        except Exception as exc:
            raise ProbeError(f"gdbserver failed: {exc}") from exc

    # -------------------------------------------------------------------------
    # Probe enumeration (offline, no session needed)
    # -------------------------------------------------------------------------

    @staticmethod
    def list_probes() -> list[dict]:
        """Return all connected debug probes without opening a session.

        Returns a list of dicts with keys: ``uid``, ``name``, ``board_name``,
        ``target``, ``vid``, ``pid``, ``info``.
        """
        from pyocd.core.helpers import ConnectHelper

        probes = []
        for info in ConnectHelper.get_all_connected_probes():
            probes.append({
                "uid":        getattr(info, "unique_id", str(info)),
                "name":       getattr(info, "name", ""),
                "board_name": getattr(info, "board_name", ""),
                "target":     getattr(info, "target", ""),
                "vid":        getattr(info, "vid", ""),
                "pid":        getattr(info, "pid", ""),
                "info":       str(info),
            })
        return probes

    @staticmethod
    def list_probes_cli() -> str:
        """Human-readable list of connected probes (runs ``pyocd list``)."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "pyocd", "list"],
            capture_output=True, text=True,
        )
        return result.stdout or result.stderr

    # -------------------------------------------------------------------------
    # Target info
    # -------------------------------------------------------------------------

    def info(self) -> dict:
        """Return a snapshot of target state.

        Runs fully inside this method — none of the reads propagate exceptions
        to the caller. A failed read returns None so the caller always gets a
        dict even if the target is in an unusual state.
        """
        def safe_halted():
            try:
                return self._target.is_halted()
            except Exception:
                return None

        def safe_reg(name: str):
            try:
                return self.register_read(name)
            except Exception:
                return None

        return {
            "halted":      safe_halted(),
            "pc":          safe_reg("pc"),
            "msp":         safe_reg("msp"),
            "psp":         safe_reg("psp"),
            "control":     safe_reg("control"),
            "primask":     safe_reg("primask"),
            "xpsr":        safe_reg("xpsr"),
            "target_type": self.target_name,
        }

    def __repr__(self):
        state = "open" if self._session else "closed"
        return f"<ProbeSession target={self.target_name!r} state={state}>"
