"""Fake drone: answers the WiFi bridge over UDP like the flight controller, for night rehearsals.

    python -m ground_station.lab.fake_drone                # 127.0.0.2:14550, control port 14551
    python -m ground_station.service --port 8081 --wifi-host 127.0.0.2
    python -m ground_station.lab.fake_drone ctl arm      # operator stand-ins: arm, disarm, reboot, vbat 15.2, sbus|ch6 off|on, quit

127.0.0.2, not 127.0.0.1: the bridge holds 0.0.0.0:14550 itself, and its keepalive nudge goes to wifi-host:14550,
so on its own loopback address the fake drone gets the nudge like the real WiFi module and replies to the sender.
Speaks the real wire protocol, so the service, `fly` and the console run unchanged:
- 0xCC 0xDE 0x21 subscribe -> 0x08 schema echo, then 0x09+slot data frames (CRC16) at 100 Hz / divider,
  values read from a memory image at the .axf addresses (livewatch.symbols resolver);
- 0xCC 0xDF transaction commands -> service.fake_drone.FakeDrone (the Workflow B state machine) -> 0x30+outcome result;
- no subscription: the 16-byte JustFloat fallback at 10 Hz, as the firmware does after boot.
`reboot` is a pack swap: silent for --silent-s, then a fresh FakeDrone with no subscriptions.
Battery: real_voltage starts at --vbat and sags SAG_V_PER_S while airborne (fake numbers, not a pack model).
Events (start, subscribe, rejects, control words, reboot, stop) go to the pipeline log (ground_station.lab.events).
"""
from __future__ import annotations

import argparse
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass

from ground_station.lab import events
from ground_station.livewatch.campaign_capture import ATTITUDE, KF_HEALTH, MOTORS, POSITION_AXES, WFB_STATUS_FIELDS
from ground_station.livewatch.transport import crc16_ccitt
from ground_station.platform.transactions import Command, Outcome, RejectReason, Result, build_command, build_result, parse_command
from ground_station.platform.wfb_commands import CMD_ARM, ArmIdx
from ground_station.service.campaign_live import VBAT_SYMS
from ground_station.service.fake_drone import FakeDrone

DT = 0.01                  # Send_Task period: SEND_TASK_MEASURED_HZ 100
JUSTFLOAT_EVERY = 10       # ticks between fallback frames (10 Hz)
SAG_V_PER_S = 0.005        # fake: about 0.65 V over a 130 s flight
ARM_SYM = "DroneStatus.ARM_Status"
MOTOR_STANDIN = 3000.0     # armed motor output: mid of the abort monitor's saturation band (2005, 3995); not a measured
                           # hover PWM. Unserved memory reads 0, which tripped motor_saturation 2 s after GO (10-14 night)
HOST = "127.0.0.2"
JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"


@dataclass
class _Sub:
    divider: int
    transport: int
    ranges: list[tuple[int, int, int]]   # (address, size, count)
    seq: int = 0


def _frame(frame_type: int, byte5: int, payload: bytes, crc16: bool = False) -> bytes:
    """AA BB TYPE LEN_HI LEN_LO BYTE5 PAYLOAD CRC: CRC16-CCITT (big-endian) for data frames, else XOR."""
    head = bytes((frame_type, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF, byte5 & 0xFF)) + payload
    if crc16:
        return b"\xAA\xBB" + head + crc16_ccitt(head).to_bytes(2, "big")
    x = 0
    for b in head:
        x ^= b
    return b"\xAA\xBB" + head + bytes((x,))


def _xor(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


class FakeUdpDrone:
    def __init__(self, resolver=None, port: int = 14550, ctl_port: int = 14551, silent_s: float = 3.0,
                 vbat: float = 16.4, host: str = HOST, names: list[str] | None = None):
        if resolver is None:
            from ground_station.livewatch.symbols import shared_resolver
            from ground_station.vofa_studio import core as vcore
            resolver = shared_resolver(vcore.ELF)
        self.resolver = resolver
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.ctl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctl.bind((host, ctl_port))
        self.port, self.ctl_port = self.sock.getsockname()[1], self.ctl.getsockname()[1]
        self.silent_s, self.pack_v = silent_s, vbat
        self.ch6 = True                       # RC ch6 HIGH (OF hold), as the checklist sets it; a reboot keeps it
        self.peer: tuple[str, int] | None = None
        self.mem: dict[int, int] = {}
        self.counts: dict[str, int] = {}
        self.ticks = 0
        self._silent_until = 0.0
        self._stop = False
        self._boot()
        self.syms = {}
        for name in names if names is not None else self._values():
            try:
                self.syms[name] = resolver.resolve(name)
            except Exception as e:  # a name this .axf lacks: logged once, never streamed
                events.log("fake_drone_unresolved", name=name, error=repr(e))

    # ------------------------------------------------------------ state
    def _boot(self) -> None:
        self.drone = FakeDrone()
        self.subs: dict[int, _Sub] = {}
        self.vbat = self.pack_v
        self.t0 = time.monotonic()

    @property
    def silent(self) -> bool:
        return time.monotonic() < self._silent_until

    def _poke(self, name: str, value: float) -> None:
        sym = self.syms.get(name)
        if sym is None or not sym.fmt:
            return
        v = bool(value) if sym.fmt == "?" else float(value) if sym.fmt in "fd" else int(round(value))
        try:
            raw = struct.pack("<" + sym.fmt, v)
        except struct.error:                                     # e.g. a negative value into an unsigned field
            self._count("poke_error")
            return
        for i, b in enumerate(raw):
            self.mem[sym.address + i] = b

    def value(self, name: str) -> float:
        sym = self.syms[name]
        return struct.unpack("<" + sym.fmt, bytes(self.mem.get(sym.address + i, 0) for i in range(sym.size)))[0]

    def _t_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000) & 0xFFFFFFFF

    def _values(self) -> dict[str, float]:
        """Every symbol the fake drone serves, by name, as the firmware would hold it now. Other memory reads 0."""
        d, st = self.drone, self.drone.status()
        # firmware names; the bridge renames them status.motor_idle / status.sbus_lost / status.rc_authority
        vals = {ARM_SYM: float(d.armed), "g_motor_idle_enabled": float(d.motors_idle),
                "sbus_lost": float(not d.sbus_live), "s_authority": 0.0, "g_of_hold_active": float(self.ch6),
                KF_HEALTH: 1.0, "xTickCount": float(self._t_ms()), VBAT_SYMS[0]: self.vbat,
                "traj_status": st.get("traj_status", 0.0), "traj_active": st.get("traj_active", 0.0)}
        vals.update(zip(ATTITUDE, (getattr(d, "roll_deg", 0.0), getattr(d, "pitch_deg", 0.0), d.yaw_deg)))
        vals.update((f"g_wfb_status.{k}", st[k]) for k in WFB_STATUS_FIELDS if k in st)
        vals.update((m, MOTOR_STANDIN if d.armed else 0.0) for m in MOTORS)
        for a, pos_m in zip(POSITION_AXES, d.position):
            vals[a.feedback] = vals[a.reference] = pos_m / a.to_m
        return vals

    def _sync(self) -> None:
        for name, v in self._values().items():
            self._poke(name, v)

    # ------------------------------------------------------------ wire in
    def _count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def handle(self, data: bytes, addr) -> None:
        if self.silent:
            return
        self.peer = addr
        i = 0
        while i < len(data):
            if data[i] != 0xCC or i + 1 >= len(data):
                i += 1
                continue
            kind = data[i + 1]
            if kind == 0xDF and i + 15 <= len(data):            # transaction command, fixed 15 bytes
                self._on_command(data[i:i + 15]); i += 15
            elif kind == 0xDE and i + 6 <= len(data) and data[i + 2] == 0x21:
                n = data[i + 5]
                end = i + 9 + 8 * n + 1
                if end <= len(data) and _xor(data[i + 2:end - 1]) == data[end - 1]:
                    self._on_subscribe(data[i + 5:end - 1])
                else:
                    self._count("bad_subscribe")
                i = max(end, i + 2)
            elif kind == 0xDD and i + 9 <= len(data):            # legacy command: no result frame
                self._count(f"legacy_0x{data[i + 2]:02X}"); i += 9
            else:
                i += 1

    def _on_command(self, frame: bytes) -> None:
        try:
            cmd = parse_command(frame)
        except Exception:
            self._count("bad_command")
            return
        outcome = Outcome(self.drone.send(frame))
        self._count(f"cmd_0x{cmd.command_id:02X}")
        reason = RejectReason.NONE
        if outcome == Outcome.REJECTED:
            reason = int(getattr(self.drone, "_last_err", 0)) or 1   # the wfb_err_t byte as the firmware sends it; 1 STATE
            events.log("fake_drone_reject", cmd=f"0x{cmd.command_id:02X}", index=cmd.index, value=cmd.value, reason=int(reason))
        self._send(build_result(Result(cmd.transaction_id, outcome, cmd.command_id, cmd.index, reason)))

    def _on_subscribe(self, body: bytes) -> None:
        n, divider, transport, slot = body[0], body[1], body[2], body[3]
        ranges = [struct.unpack_from("<IHH", body, 4 + 8 * k) for k in range(n)]
        if divider == 0:
            self.subs.pop(slot, None)
            events.log("fake_drone_unsubscribe", slot=slot)
            return
        self.subs[slot] = _Sub(divider, transport, ranges)
        total = sum(size * count for _, size, count in ranges)
        echo = struct.pack(">BBBH", divider, transport, slot, total) + b"".join(struct.pack("<IHH", *r) for r in ranges)
        self._send(_frame(0x08, n, echo))
        events.log("fake_drone_subscribe", slot=slot, divider=divider, ranges=n, bytes=total)

    # ------------------------------------------------------------ wire out
    def _send(self, frame: bytes) -> None:
        if self.peer is not None and not self.silent:
            try:
                self.sock.sendto(frame, self.peer)
            except OSError:
                self._count("send_error")

    def tick(self) -> None:
        self.ticks += 1
        if self.silent:
            return
        if self._silent_until:                                   # first tick after a reboot
            self._silent_until = 0.0
            self._boot()
            events.log("fake_drone_back")
        self.drone.step(DT)
        if self.drone.status().get("prim_state", 0) > 0:
            self.vbat -= SAG_V_PER_S * DT
        self._sync()
        t_ms = self._t_ms()
        for slot, sub in self.subs.items():
            if self.ticks % sub.divider:
                continue
            payload = struct.pack("<I", t_ms) + b"".join(
                bytes(self.mem.get(a + k, 0) for k in range(size * count)) for a, size, count in sub.ranges)
            self._send(_frame(0x09 + slot, sub.seq, payload, crc16=True))
            sub.seq = (sub.seq + 1) & 0xFF
        if not self.subs and self.ticks % JUSTFLOAT_EVERY == 0:
            self._send(struct.pack("<fff", 0.0, 0.0, 0.0) + JUSTFLOAT_TAIL)

    # ------------------------------------------------------------ operator stand-ins
    def control(self, word: str) -> str:
        parts = word.split()
        if not parts:
            return "error: empty"
        op = parts[0]
        if op == "arm":                                          # RC arm + motor init, through the real handlers
            reply = "ok arm"
            for idx in (ArmIdx.ARM, ArmIdx.IDLE):
                out = Outcome(self.drone.send(build_command(Command(0, CMD_ARM, idx, 1.0))))
                if out != Outcome.APPLIED:
                    reply = f"refused: arm step {idx.name} {out.name}"
                    break
        elif op == "disarm":
            out = Outcome(self.drone.send(build_command(Command(0, CMD_ARM, ArmIdx.ARM, 0.0))))
            reply = "ok disarm" if out == Outcome.APPLIED else "refused: airborne"
        elif op == "reboot":
            self._silent_until = time.monotonic() + self.silent_s
            self.subs = {}
            reply = f"ok reboot, silent {self.silent_s:g} s"
        elif op == "vbat" and len(parts) == 2:
            try:
                self.vbat = self.pack_v = float(parts[1])
                reply = f"ok vbat {self.vbat:.2f}"
            except ValueError:
                reply = f"error: vbat needs a number, got {parts[1]!r}"
        elif op == "sbus" and len(parts) == 2 and parts[1] in ("on", "off"):
            self.drone.sbus_live = parts[1] == "on"
            reply = f"ok sbus {parts[1]}"
        elif op == "ch6" and len(parts) == 2 and parts[1] in ("on", "off"):
            self.ch6 = parts[1] == "on"
            reply = f"ok ch6 {parts[1]}"
        elif op == "quit":
            self._stop = True
            reply = "ok quit"
        else:
            reply = f"error: unknown word {word!r}"
        events.log("fake_drone_ctl", word=word, reply=reply)
        return reply

    # ------------------------------------------------------------ loop
    def run(self, until: float | None = None) -> None:
        events.log("fake_drone_start", port=self.port, ctl_port=self.ctl_port, symbols=len(self.syms))
        next_tick = time.monotonic()
        try:
            while not self._stop and (until is None or time.monotonic() < until):
                wait = max(0.0, next_tick - time.monotonic())
                ready, _, _ = select.select([self.sock, self.ctl], [], [], wait)
                for s in ready:
                    try:
                        data, addr = s.recvfrom(4096)
                    except OSError:                              # Windows: ICMP port unreachable from a gone peer
                        continue
                    if s is self.sock:
                        self.handle(data, addr)
                    else:
                        self.ctl.sendto(self.control(data.decode("utf-8", "replace").strip()).encode(), addr)
                now = time.monotonic()
                if now - next_tick > 0.5:                        # stalled (debugger, sleep): do not burst
                    next_tick = now
                while now >= next_tick:
                    self.tick()
                    next_tick += DT
        finally:
            events.log("fake_drone_stop", ticks=self.ticks, **self.counts)

    def close(self) -> None:
        self.sock.close()
        self.ctl.close()


def ctl(word: str, port: int = 14551, host: str = HOST, timeout_s: float = 2.0) -> str:
    """Send one control word to a running fake drone and return its reply."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout_s)
    try:
        s.sendto(word.encode(), (host, port))
        return s.recvfrom(4096)[0].decode()
    except OSError as e:
        return f"error: no reply from the fake drone on {port} ({e})"
    finally:
        s.close()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["ctl"]:
        reply = ctl(" ".join(argv[1:]))
        print(reply)
        return 0 if reply.startswith("ok") else 1
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=14550)
    ap.add_argument("--ctl-port", type=int, default=14551)
    ap.add_argument("--silent-s", type=float, default=3.0, help="reboot silence (pack swap)")
    ap.add_argument("--vbat", type=float, default=16.4)
    a = ap.parse_args(argv)
    drone = FakeUdpDrone(port=a.port, ctl_port=a.ctl_port, silent_s=a.silent_s, vbat=a.vbat)
    print(f"fake drone on {HOST}:{drone.port} (control {drone.ctl_port}), {len(drone.syms)} symbols", flush=True)
    try:
        drone.run()
    except KeyboardInterrupt:
        pass
    finally:
        drone.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
