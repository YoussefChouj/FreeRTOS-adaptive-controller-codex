"""Command-line front end for livewatch.

  python -m ground_station.livewatch names [--filter STR]
  python -m ground_station.livewatch fields <symbol>
  python -m ground_station.livewatch groups
  python -m ground_station.livewatch read  <name|group:...> [names...]
  python -m ground_station.livewatch watch <name|group:...> [names...] [--hz N] [--secs S] [--csv FILE]

`names`/`fields`/`groups` need no hardware (pure DWARF). `read`/`watch` open a
read-only attach session to the running target.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from .fault_log import cmd_fault_read, cmd_fault_erase
from .registry import Registry
from .symbols import SymbolResolver
from .transport import LiveTransportError, SwdCmsisDap, Uart5LongRange, Usart3WifiSubscribeTransport
from .symbols import SymbolResolver
from .transport import LiveTransportError, SwdCmsisDap, Uart5LongRange, Usart3WifiSubscribeTransport

_DEFAULT_ELF = Path(__file__).resolve().parents[2] / "OBJ" / "JX_FLY.axf"
_DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def _resolver(args) -> SymbolResolver:
    return SymbolResolver(args.elf)


def _transport_config() -> dict[str, str]:
    out = {}
    if _DEFAULT_CONFIG.exists():
        for raw in _DEFAULT_CONFIG.read_text(errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def _transport(args):
    t = getattr(args, "transport", "wifi")
    if t == "wifi":
        cfg = _transport_config()
        port = int(cfg.get("livewatch_wifi_port", 14550))
        module_ip = cfg.get("livewatch_wifi_module_ip", "192.168.4.1")
        return Usart3WifiSubscribeTransport(port=port, module_ip=module_ip)
    if t == "uart5":
        cfg = _transport_config()
        port = getattr(args, "uart5_port", None) or cfg.get("livewatch_uart5_port", "")
        baud = getattr(args, "uart5_baud", None) or int(
            cfg.get("livewatch_uart5_baud", 115200))
        return Uart5LongRange(port=port, baud=baud)
    return SwdCmsisDap()


def _live_reader(args):
    from .reader import LiveReader
    return LiveReader(args.elf, transport=_transport(args),
                      swd_limit_packets=not getattr(args, "swd_no_limit_packets", False))


def cmd_names(args):
    r = _resolver(args)
    flt = (args.filter or "").lower()
    for n in r.names():
        if flt in n.lower():
            print(n)


def cmd_fields(args):
    r = _resolver(args)
    for f in r.fields_of(args.symbol):
        print(f)


def cmd_groups(args):
    reg = Registry()
    for g in reg.group_names():
        print(f"{g:14s} {reg.doc(g)}")
        for v in reg.vars(g):
            print(f"    {v}")


def _expand(args) -> list[str]:
    return Registry().expand(args.names)


def cmd_read(args):
    names = _expand(args)
    with _live_reader(args) as lr:
        plan = lr.plan(names)
        print(f"# {len(plan.regions)} region(s) / {len(names)} vars")
        row = lr.sample(plan)
        for n in names:
            v = row[n]
            print(f"{n:22s} {_fmt(v)}")


def cmd_watch(args):
    names = _expand(args)
    writer = None
    fh = None
    with _live_reader(args) as lr:
        try:
            for row in lr.stream(names, hz=args.hz, duration=args.secs):
                if args.csv and writer is None:
                    fh = open(args.csv, "w", newline="")
                    writer = csv.DictWriter(fh, fieldnames=list(row))
                    writer.writeheader()
                if writer:
                    writer.writerow(row)
                line = "  ".join(f"{k}={_fmt(v)}" for k, v in row.items())
                print(line)
        except KeyboardInterrupt:
            print("\n# stopped", file=sys.stderr)
        finally:
            if fh:
                fh.close()
                print(f"# wrote {args.csv}", file=sys.stderr)


def cmd_verify(args):
    """Prove OBJ/JX_FLY.axf is the build running on the target before trusting a read.

    Default chunk density is `args.chunks` (default 20) per flash segment.
    The pre-2026-08-20 default was 5 -- too sparse: a localised drift slipped
    past and gave a false "ELF matches" verdict while the FC was actually
    running different firmware (the 50-53 B MicoAir datagrams had no magic
    header that the source specifies).

    `--full` samples every 64 B chunk in every segment -- O(image_bytes /
    chunk) reads, guaranteed detection. Slow over the wireless debugger,
    fast over a wired ST-Link. Use it for a one-time reflash sanity check
    or any time the cheap sampling misses something you can reproduce.

    `--identity` reads `g_fw_identity` over SWD and compares the firmware
    CRC-32/MPEG-2 against a host-side computation from OBJ/JX_FLY.hex.
    This is a lighter-weight check than full flash comparison and works over
    the wireless debugger without hitting the flash segment budget.

    The wireless debugger throws `TransferError` in clusters (see
    `transport._read_region_with_retry` for the in-region retry). On dense
    reads one of N regions can still exhaust its retries; we wrap the
    per-chunk read in another retry layer so the verify run as a whole
    tolerates a single flaky chunk. Without this, the new 20-chunk density
    fails the run purely on probe hiccups, defeating the purpose.
    """
    from .reader import LiveReader
    from .verify import IdentityCheck, compare, flash_segments, plan_samples, read_identity

    if args.identity:
        with LiveReader(args.elf, transport=SwdCmsisDap(
                limit_packets=not getattr(args, "swd_no_limit_packets", False))) as lr:
            def _read_raw(addr, n):
                return lr.read_raw(addr, n)
            addr = None
            try:
                sym = lr.symbols.resolve("g_fw_identity")
                if sym:
                    addr = sym.address
            except Exception:
                pass
            id_check = read_identity(_read_raw, identity_addr=addr, elf_path=args.elf)
        print(f"fw_identity: magic=0x{id_check.magic:08X} ver={id_check.version} "
              f"base=0x{id_check.image_base:08X} len={id_check.image_len} "
              f"fw_crc=0x{id_check.firmware_crc:08X} host_crc=0x{id_check.host_crc:08X} "
              f"status={id_check.status}")
        print(id_check.message)
        return 0 if id_check.ok else 2

    segs = flash_segments(args.elf)
    samples = plan_samples(segs, n=args.chunks, full=args.full)
    n_full = "(every chunk)" if args.full else f"{args.chunks} per segment"
    print(f"# {len(segs)} flash segment(s), sampling {len(samples)} chunk(s) [{n_full}]",
          file=sys.stderr)
    with LiveReader(args.elf, transport=SwdCmsisDap(
            limit_packets=not getattr(args, "swd_no_limit_packets", False))) as lr:
        def _read_with_chunk_retry(addr, n):
            delay = 0.1
            for attempt in range(4):
                try:
                    return bytes(lr.read_raw(addr, n))
                except Exception as exc:
                    if attempt >= 3:
                        raise
                    import time as _time
                    print(f"# chunk 0x{addr:08X} read failed (attempt {attempt+1}/4): "
                          f"{type(exc).__name__}; retrying in {delay:.1f} s",
                          file=sys.stderr)
                    _time.sleep(delay)
                    delay = min(delay * 2, 2.0)
            raise RuntimeError("unreachable")
        res = compare(samples, _read_with_chunk_retry)
    print(res.describe())
    return 0 if res.ok else 2


def cmd_manifests(args):
    from .manifest import ManifestStore
    store = ManifestStore()
    for n in store.names():
        m = store.get(n)
        doc = " ".join(m.doc.split())
        print(f"{n:16s} {m.hz:>5g} Hz  {len(m.vars):>3} vars   {doc[:90]}")


def cmd_transports(args):
    for transport in (SwdCmsisDap(), Uart5LongRange(port="CONFIGURED")):
        print(f"{transport.name:8s} {transport.cost_model.describe()}")


# -------------------------------------------------------------------------
# Full probe subcommands (write paths unlocked — research drone)
# -------------------------------------------------------------------------

def _probe(args):
    from .probe import ProbeSession, ProbeError
    return ProbeSession(
        target=getattr(args, "target", "cortex_m"),
        connect_mode=getattr(args, "connect_mode", "attach"),
        resume_on_disconnect=getattr(args, "resume_on_disconnect", False),
        cmsis_dap_limit_packets=not getattr(args, "swd_no_limit_packets", False),
        probe_descriptors=getattr(args, "probe", None),
    )


def cmd_probe_list(args):
    """List all connected debug probes (offline, no session)."""
    from .probe import ProbeSession, ProbeError
    try:
        probes = ProbeSession.list_probes()
        if not probes:
            print("no probes found")
            return
        print(f"{'Name':<20} {'Board':<20} {'Target':<15} UID")
        print("-" * 90)
        for p in probes:
            print(f"{p['name']:<20} {p['board_name']:<20} "
                  f"{p['target']:<15} {p['uid']}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_registers(args):
    """Read one or more core registers."""
    from .probe import CORE_REGISTERS, ProbeError
    if args.all_regs:
        names = CORE_REGISTERS
    else:
        names = args.names

    try:
        with _probe(args) as probe:
            if args.halt:
                probe.halt()
                print("[probe] halted")
            if args.all_regs or len(names) > 1:
                regs = probe.registers_read()
                print(probe.registers_dump())
            else:
                v = probe.register_read(names[0])
                print(f"{names[0]:>12s}  = 0x{v:08X}  ({v})")
            if args.halt:
                probe.resume()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_halt(args):
    """Halt the core."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            probe.halt()
            regs = probe.registers_read()
            print(probe.registers_dump())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_resume(args):
    """Resume the core."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            probe.resume()
            print("[probe] resumed")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_step(args):
    """Single-step one instruction."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            probe.halt()
            before = probe.register_read("pc")
            probe.step()
            after = probe.register_read("pc")
            print(f"[probe] pc 0x{before:08X} -> 0x{after:08X}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_reset(args):
    """Reset the core."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            mode = getattr(args, "mode", "system")
            if args.halt_after:
                probe.reset_halt()
                print(f"[probe] reset (halt-after)  mode={mode}")
                print(probe.registers_dump())
            else:
                probe.reset(mode=mode)
                print(f"[probe] reset  mode={mode}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_peek(args):
    """Read memory (8/16/32/64 bit or block)."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            if args.size == "block":
                data = probe.read_memory_block8(addr, args.count)
                print(probe.dump_memory(addr, args.count)["hex"])
            elif args.size == "dword":
                v = probe.read_memory(addr, 64)
                print(f"0x{v:016X}  ({v})")
            elif args.size == "word":
                v = probe.read_memory(addr, 32)
                print(f"0x{v:08X}  ({v})")
            elif args.size == "hword":
                v = probe.read_memory(addr, 16)
                print(f"0x{v:04X}  ({v})")
            else:
                v = probe.read_memory(addr, 8)
                print(f"0x{v:02X}  ({v})")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_poke(args):
    """Write memory (8/16/32/64 bit or block)."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            if args.data.lower().startswith("0x"):
                value = int(args.data, 0)
            else:
                value = int(args.data)
            sizes = {"byte": 8, "hword": 16, "word": 32, "dword": 64}
            size = sizes.get(args.size, 32)
            probe.write_memory(addr, value, size=size)
            verify = probe.read_memory(addr, size)
            ok = "OK" if verify == value else f"MISMATCH got 0x{verify:X}"
            print(f"[probe] poke 0x{addr:08X} = 0x{value:X} ({ok})")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_dump(args):
    """Hexdump a memory region."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            size = args.size
            rowsz = 16
            for off in range(0, size, rowsz):
                chunk = probe.read_memory_block8(addr + off, min(rowsz, size - off))
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"  {addr+off:08X}  {hex_part:<48s}  {asc_part}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_flash_read(args):
    """Read flash memory (address from ELF section or manual)."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            size = int(args.size, 0)
            data = probe.read_flash(addr, size)
            rowsz = 16
            print(f"# flash 0x{addr:08X} +{size} B")
            for off in range(0, size, rowsz):
                chunk = data[off:off+rowsz]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"  {addr+off:08X}  {hex_part:<48s}  {asc_part}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_flash_write(args):
    """Write raw bytes or an Intel HEX / binary file to flash."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            path = Path(args.file).expanduser()
            if args.base is not None:
                addr = int(args.base, 0)
                data = path.read_bytes()
                probe.write_flash(addr, data)
            else:
                probe.flash_elf(path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_flash_erase(args):
    """Erase flash sectors."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0) if args.address else 0
            size = int(args.size, 0) if args.size else None
            probe.erase_flash(addr=addr, size=size)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_bp_set(args):
    """Set a hardware or software breakpoint."""
    from .probe import ProbeError, BreakpointKind
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            kind = BreakpointKind.HW if args.hw else BreakpointKind.SW
            probe.bp_set(addr, kind)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_bp_remove(args):
    """Remove a breakpoint by address."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            probe.bp_remove(addr)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_bp_list(args):
    """List active breakpoints."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            bps = probe.bp_list()
            if not bps:
                print("no breakpoints set")
            for bp in bps:
                print(f"  0x{bp['addr']:08X}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_wp_set(args):
    """Set a watchpoint."""
    from .probe import ProbeError, WatchpointType
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            kind_map = {"r": WatchpointType.READ, "w": WatchpointType.WRITE, "a": WatchpointType.ACCESS}
            kind = kind_map.get(args.type.lower()[0], WatchpointType.ACCESS)
            size = getattr(args, "size", 4)
            probe.wp_set(addr, size=size, kind=kind)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_wp_remove(args):
    """Remove a watchpoint."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            addr = int(args.address, 0)
            probe.wp_remove(addr)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_rtt_list(args):
    """List available RTT channels."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            info = probe.rtt_list_channels()
            print("Up channels:")
            for ch in info.get("up", []):
                print(f"  [{ch['index']}] {ch['name']}  buf_size={ch['buf_size']}")
            print("Down channels:")
            for ch in info.get("down", []):
                print(f"  [{ch['index']}] {ch['name']}  buf_size={ch['buf_size']}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_rtt_read(args):
    """Read from an RTT up-channel."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            captures = probe.rtt_read_channel(channel=args.channel, timeout_s=args.timeout)
            for cap in captures:
                try:
                    print(cap.data.decode("utf-8", errors="replace"), end="")
                except Exception:
                    print(f"<binary {len(cap.data)} B>", end="")
            if not captures:
                print("(no data)", end="")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_rtt_write(args):
    """Write a string to an RTT down-channel."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            data = args.data
            if data.startswith("0x"):
                data = bytes.fromhex(data[2:])
            probe.rtt_write_channel(args.channel, data)
            print(f"[probe] wrote {len(data) if isinstance(data, bytes) else len(data.encode())} B to RTT channel {args.channel}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_rtt_telnet(args):
    """Start a telnet server bridging RTT channel 0."""
    from .probe import ProbeError
    import threading
    try:
        def run():
            with _probe(args) as probe:
                probe.rtt_telnet(port=args.port)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        print(f"[probe] RTT telnet server started on port {args.port}")
        print("[probe] connect with:  telnet localhost", args.port)
        print("[probe] (server running in background thread)")
        time.sleep(args.duration)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_swo_read(args):
    """Read accumulated SWO trace data."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            probe.swo_start(baud=args.baud)
            captures = probe.swo_read(timeout_s=args.timeout)
            for cap in captures:
                print(f"# SWO {len(cap.data)} B at t={cap.timestamp_s:.3f}")
                hex_part = " ".join(f"{b:02X}" for b in cap.data)
                print(f"  {hex_part}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_gdbserver(args):
    """Start a GDB server on a TCP port."""
    from .probe import ProbeError
    import threading
    try:
        def run():
            with _probe(args) as probe:
                probe.gdbserver_start(port=args.port, telnet_port=args.telnet_port)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        print(f"[probe] GDB server listening on :{args.port}")
        print(f"[probe] connect:  target remote localhost:{args.port}")
        print("[probe] (server running in background thread)")
        time.sleep(args.duration)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_probe_info(args):
    """Print target state snapshot (pc, msp, psp, primask, etc.)."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            info = probe.info()
            halted = "HALTED" if info["halted"] else "running"
            print(f"Core state : {halted}")

            def _hex(v):
                return f"0x{v:08X}" if v is not None else "unavailable"

            print(f"PC          : {_hex(info['pc'])}")
            print(f"MSP         : {_hex(info['msp'])}")
            print(f"PSP         : {_hex(info['psp'])}")
            print(f"CONTROL     : {_hex(info['control'])}")
            print(f"PRIMASK     : {_hex(info['primask'])}")
            print(f"XPSR        : {_hex(info['xpsr'])}")
            print(f"Target      : {info['target_type']}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_probe_session(args):
    """Open a probe session and run an inline Python snippet."""
    from .probe import ProbeError
    try:
        with _probe(args) as probe:
            print(f"# probe session open: {probe}")
            print(f"# Available: probe.read_memory(), probe.write_memory(),")
            print(f"#             probe.registers_read(), probe.flash_elf(),")
            print(f"#             probe.bp_set(), probe.rtt_read_channel(), etc.")
            print(f"# Inspect state: probe.info()")
            print(f"# Dump regs    : probe.registers_dump()")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _manifest_for(args):
    """Either a named manifest or an ad-hoc one built from --vars."""
    from .manifest import ManifestStore
    store = ManifestStore()
    if args.vars:
        return store.adhoc(args.vars, hz=args.hz or 20.0, name=args.name or "adhoc")
    m = store.get(args.manifest)
    if args.hz:
        m.hz = args.hz
    return m


def cmd_budget(args):
    """Feasible sample rate for a manifest. Pure DWARF + cost model, no hardware."""
    from .manifest import feasibility
    from .reader import build_plan
    m = _manifest_for(args)
    transport = _transport(args)
    plan = build_plan(_resolver(args), m.vars, transport.gap_merge_bytes)
    feas = feasibility(plan, cost_model=transport.cost_model)
    print(f"{m.name}: {feas.describe()}")
    if feas.ok_for(m.hz):
        print(f"  requested {m.hz:g} Hz -> OK ({m.hz / feas.max_hz * 100:.0f}% of ceiling)")
    else:
        print(f"  requested {m.hz:g} Hz -> TOO FAST, ceiling is ~{feas.max_hz:.0f} Hz")
    for r in plan.regions:
        print(f"    region 0x{r.start:08X} +{r.size} B")


def cmd_log(args):
    """Log a manifest to a uniquely named CSV, after checking the rate is real."""
    from .manifest import unique_csv_path, write_meta
    m = _manifest_for(args)
    with _live_reader(args) as lr:
        plan = lr.plan(m.vars)
        feas = lr.transport.calibrate(lr, plan)
        print(f"# {m.name}: {feas.describe()}", file=sys.stderr)

        hz = m.hz
        if not feas.ok_for(hz):
            if args.clamp:
                hz = feas.max_hz
                print(f"# requested {m.hz:g} Hz exceeds the ceiling; clamped to {hz:.0f} Hz",
                      file=sys.stderr)
            else:
                print(f"ERROR: {m.name} cannot sustain {hz:g} Hz; measured ceiling is "
                      f"{feas.max_hz:.0f} Hz.\n"
                      f"       Re-run with --hz {feas.max_hz:.0f} or fewer vars, or pass "
                      f"--clamp to log at the ceiling.", file=sys.stderr)
                return 1

        out = unique_csv_path(args.outdir, m, hz)
        meta = write_meta(out, m, plan, args.elf, requested_hz=m.hz, feas=feas,
                          extra={"logged_hz": hz,
                                 "transport": lr.transport.cost_model.transport_name})
        print(f"# -> {out}\n# -> {meta}", file=sys.stderr)

        n = 0
        t_last = None
        fh = open(out, "w", newline="")
        writer = None
        try:
            for row in lr.stream(m.vars, hz=hz, duration=args.secs):
                if writer is None:
                    writer = csv.DictWriter(fh, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                n += 1
                t_last = row["t"]
                if args.quiet:
                    if n % 50 == 0:
                        print(f"\r# {n} samples", end="", file=sys.stderr)
                else:
                    print("  ".join(f"{k}={_fmt(v)}" for k, v in row.items()))
        except KeyboardInterrupt:
            print("\n# stopped", file=sys.stderr)
        finally:
            fh.close()
            rate = (n / t_last) if t_last else 0.0
            print(f"\n# wrote {n} samples to {out} (effective {rate:.1f} Hz)", file=sys.stderr)
    return 0


def _fmt(v):
    if isinstance(v, float):
        return f"{v:+.5g}"
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)}B>"
    return str(v)


def build_parser():
    p = argparse.ArgumentParser(prog="livewatch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--elf", default=str(_DEFAULT_ELF), help="firmware ELF (default OBJ/JX_FLY.axf)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("names", help="list resolvable base symbols")
    sp.add_argument("--filter", help="substring filter (case-insensitive)")
    sp.set_defaults(func=cmd_names)

    sp = sub.add_parser("fields", help="list members/elements under a symbol")
    sp.add_argument("symbol")
    sp.set_defaults(func=cmd_fields)

    sp = sub.add_parser("groups", help="list registry watch groups")
    sp.set_defaults(func=cmd_groups)

    sp = sub.add_parser("transports", help="list live-read transports and cost models")
    sp.set_defaults(func=cmd_transports)

    sp = sub.add_parser("read", help="one-shot read (needs hardware)")
    sp.add_argument("names", nargs="+", help="paths and/or group:<name> tokens")
    _transport_args(sp)
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("watch", help="stream at N Hz (needs hardware)")
    sp.add_argument("names", nargs="+", help="paths and/or group:<name> tokens")
    sp.add_argument("--hz", type=float, default=20.0)
    sp.add_argument("--secs", type=float, default=None, help="stop after S seconds")
    sp.add_argument("--csv", help="also log samples to CSV")
    _transport_args(sp)
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("verify", help="check the ELF matches the flashed firmware (needs hardware)")
    sp.add_argument("--chunks", type=int, default=20,
                    help="chunks sampled per flash segment (default 20, "
                         "was 5 pre-2026-08-20; --full overrides)")
    sp.add_argument("--full", action="store_true",
                    help="sample every chunk (slow over wireless SWD; "
                         "use a wired ST-Link for this)")
    sp.add_argument("--identity", action="store_true",
                    help="read g_fw_identity over SWD and compare the firmware "
                         "CRC-32/MPEG-2 against a host-side computation from "
                         "OBJ/JX_FLY.hex")
    sp.add_argument("--swd-no-limit-packets", action="store_true",
                    help="disable single-in-flight USB mode for SWD reads "
                         "(use only for wired probes where throughput matters more than reliability)")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("manifests", help="list logging manifests")
    sp.set_defaults(func=cmd_manifests)

    sp = sub.add_parser("budget", help="feasible sample rate for a manifest (no hardware)")
    _manifest_args(sp)
    _transport_args(sp)
    sp.set_defaults(func=cmd_budget)

    sp = sub.add_parser("log", help="log a manifest to a uniquely named CSV (needs hardware)")
    _manifest_args(sp)
    _transport_args(sp)
    sp.add_argument("--secs", type=float, default=None, help="stop after S seconds")
    sp.add_argument("--outdir", default="logs/livewatch", help="CSV output directory")
    sp.add_argument("--clamp", action="store_true",
                    help="log at the measured ceiling instead of refusing when --hz is too fast")
    sp.add_argument("--quiet", action="store_true", help="progress counter instead of every row")
    sp.set_defaults(func=cmd_log)

    # ---- Subscribe / WiFi subcommands (added 2026-09-13) -----------------

    def _wifi_args(sp):
        sp.add_argument("--host", default="192.168.4.1",
                        help="MicoAir WiFi AP IP (default 192.168.4.1)")
        sp.add_argument("--port", type=int, default=14550,
                        help="UDP port (default 14550)")
        return sp

    sp = sub.add_parser(
        "list",
        help="list DWARF symbols (alias of 'names')",
        description="List all known DWARF symbol base names. Equivalent to 'names'.",
    )
    sp.add_argument("--filter", help="substring filter (case-insensitive)")
    sp.set_defaults(func=cmd_names)

    sp = sub.add_parser(
        "peekvar",
        help="peek a DWARF-resolved variable by name (needs hardware)",
        description=(
            "Read a single firmware variable once by DWARF name (e.g. "
            "'DroneStatus.ARM_Status'). Uses the same LiveReader as 'read'; "
            "this command exists for callers that prefer a 'peek <name>' verb. "
            "NOTE: 'peek' (without 'var') reads a MEMORY ADDRESS on the probe "
            "interface -- the names differ on purpose."),
    )
    sp.add_argument("name", help="DWARF variable path (e.g. 'DroneStatus.ARM_Status')")
    _transport_args(sp)
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser(
        "clear",
        help="pre-clear all 4 FC subscribe slots (idempotent)",
        description=(
            "Stop every active subscribe slot on the FC and wait for the wire "
            "to go quiet. Safe to run twice -- the second call confirms a "
            "clean state and returns finished_clean=True."),
    )
    sp.add_argument("--rounds", type=int, default=5,
                    help="stop-then-drain cycles (default 5; 3 is enough for "
                         "warm FCs)")
    sp.add_argument("--drain-secs", type=float, default=2.5,
                    help="total drain time across all rounds, seconds (default 2.5)")
    _wifi_args(sp)
    sp.set_defaults(func=cmd_clear)

    sp = sub.add_parser(
        "probe-slots",
        help="probe liveness of all 4 FC subscribe slots",
        description=(
            "Sends a stop request to each FC slot and watches for any reply, "
            "giving a per-slot liveness signal. NOTE: the FC-side s_streams[] "
            "struct is `static` (API/subscribe.c:370) and not reachable via "
            "DWARF, so we cannot directly read the active flags. This command "
            "uses the wire round-trip as a proxy -- quiet means the link is "
            "alive; no traffic could mean the slot is unused or the FC is "
            "offline. For full slot introspection use the GUI multi-slot panel."),
    )
    sp.add_argument("--per-slot-window", type=float, default=0.5,
                    help="observation window per slot, seconds (default 0.5)")
    _wifi_args(sp)
    sp.set_defaults(func=cmd_probe_slots)

    # ---- Full probe subcommands (write paths unlocked) ----

    def _probe_args(sp):
        sp.add_argument("--target", default="cortex_m", help="pyOCD target name")
        sp.add_argument("--connect-mode", default="attach",
                        choices=["attach", "halt"], help="connect mode")
        sp.add_argument("--resume-on-disconnect", action="store_true",
                        help="resume core when session closes")
        sp.add_argument("--swd-no-limit-packets", action="store_true",
                        help="disable single-in-flight USB mode")
        sp.add_argument("--probe", help="probe descriptor (USB VID/PID/unique ID)")
        return sp

    sp = sub.add_parser("probes", help="list all connected debug probes (offline)")
    sp.set_defaults(func=cmd_probe_list)

    sp = sub.add_parser("registers", help="read one or more core registers")
    sp.add_argument("names", nargs="*", help="register names (or --all)")
    sp.add_argument("--all", dest="all_regs", action="store_true",
                    help="read all available core registers")
    sp.add_argument("--halt", action="store_true", help="halt core first")
    _probe_args(sp)
    sp.set_defaults(func=cmd_registers)

    sp = sub.add_parser("halt", help="halt the core")
    _probe_args(sp)
    sp.set_defaults(func=cmd_halt)

    sp = sub.add_parser("resume", help="resume the core")
    _probe_args(sp)
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("step", help="single-step one instruction")
    _probe_args(sp)
    sp.set_defaults(func=cmd_step)

    sp = sub.add_parser("reset", help="reset the core")
    sp.add_argument("--mode", default="system",
                    choices=["system", "core", "hw"],
                    help="reset type (default system)")
    sp.add_argument("--halt-after", action="store_true",
                    help="halt immediately after reset")
    _probe_args(sp)
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("peek", help="read memory (8/16/32/64 bit or block)")
    sp.add_argument("address", help="address (hex, e.g. 0x20000000)")
    sp.add_argument("--size", "-s", default="word",
                    choices=["byte", "hword", "word", "dword", "block"],
                    help="access size (default word = 32 bit)")
    sp.add_argument("--count", "-n", type=int, default=64,
                    help="byte count for block reads")
    _probe_args(sp)
    sp.set_defaults(func=cmd_peek)

    sp = sub.add_parser("poke", help="write memory (8/16/32/64 bit)")
    sp.add_argument("address", help="address (hex)")
    sp.add_argument("data", help="value (decimal or 0xHEX)")
    sp.add_argument("--size", "-s", default="word",
                    choices=["byte", "hword", "word", "dword"])
    _probe_args(sp)
    sp.set_defaults(func=cmd_poke)

    sp = sub.add_parser("dump", help="hexdump a memory region")
    sp.add_argument("address", help="start address (hex)")
    sp.add_argument("size", type=int, help="size in bytes")
    _probe_args(sp)
    sp.set_defaults(func=cmd_dump)

    sp = sub.add_parser("flash-read", help="read flash memory")
    sp.add_argument("address", help="flash address (hex)")
    sp.add_argument("size", help="size in bytes (hex or decimal)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_flash_read)

    sp = sub.add_parser("flash-write", help="write to flash (binary/hex/ELF)")
    sp.add_argument("file", help="file to write (binary, HEX, or ELF)")
    sp.add_argument("--base", help="address for binary/HEX (default: use ELF sections)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_flash_write)

    sp = sub.add_parser("flash-erase", help="erase flash")
    sp.add_argument("--address", help="start address (hex)")
    sp.add_argument("--size", help="size to erase in bytes (hex or decimal)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_flash_erase)

    sp = sub.add_parser("bp", help="set a hardware/software breakpoint")
    sp.add_argument("address", help="address (hex)")
    sp.add_argument("--sw", dest="hw", action="store_false",
                    help="use software breakpoint (default: hardware)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_bp_set)

    sp = sub.add_parser("bp-remove", help="remove breakpoint")
    sp.add_argument("address", help="address (hex)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_bp_remove)

    sp = sub.add_parser("bp-list", help="list active breakpoints")
    _probe_args(sp)
    sp.set_defaults(func=cmd_bp_list)

    sp = sub.add_parser("wp", help="set a watchpoint")
    sp.add_argument("address", help="address (hex)")
    sp.add_argument("--type", "-t", default="access",
                    choices=["read", "write", "access"],
                    help="watchpoint type (default access)")
    sp.add_argument("--size", "-s", type=int, default=4,
                    help="size in bytes (default 4)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_wp_set)

    sp = sub.add_parser("wp-remove", help="remove watchpoint")
    sp.add_argument("address", help="address (hex)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_wp_remove)

    sp = sub.add_parser("rtt-list", help="list available RTT channels")
    _probe_args(sp)
    sp.set_defaults(func=cmd_rtt_list)

    sp = sub.add_parser("rtt-read", help="read from RTT up-channel (target -> host)")
    sp.add_argument("--channel", type=int, default=0, help="RTT channel (default 0)")
    sp.add_argument("--timeout", type=float, default=1.0, help="timeout in seconds")
    _probe_args(sp)
    sp.set_defaults(func=cmd_rtt_read)

    sp = sub.add_parser("rtt-write", help="write to RTT down-channel (host -> target)")
    sp.add_argument("data", help="string or 0xHEX bytes to send")
    sp.add_argument("--channel", type=int, default=0)
    _probe_args(sp)
    sp.set_defaults(func=cmd_rtt_write)

    sp = sub.add_parser("rtt-telnet", help="start RTT telnet server")
    sp.add_argument("--port", type=int, default=20294)
    sp.add_argument("--duration", type=float, default=60.0,
                    help="keep running for this many seconds")
    _probe_args(sp)
    sp.set_defaults(func=cmd_rtt_telnet)

    sp = sub.add_parser("swo-read", help="read SWO trace data")
    sp.add_argument("--baud", type=int, default=1000000, help="SWO baud (default 1 MHz)")
    sp.add_argument("--timeout", type=float, default=2.0, help="timeout in seconds")
    _probe_args(sp)
    sp.set_defaults(func=cmd_swo_read)

    sp = sub.add_parser("gdbserver", help="start GDB server on TCP port")
    sp.add_argument("--port", type=int, default=3333, help="GDB port (default 3333)")
    sp.add_argument("--telnet-port", type=int, default=4444)
    sp.add_argument("--duration", type=float, default=60.0)
    _probe_args(sp)
    sp.set_defaults(func=cmd_gdbserver)

    sp = sub.add_parser("probe-info", help="print target state snapshot (PC, MSP, PRIMASK...)")
    _probe_args(sp)
    sp.set_defaults(func=cmd_probe_info)

    sp = sub.add_parser("probe-session", help="open a probe session and print what's available")
    _probe_args(sp)
    sp.set_defaults(func=cmd_probe_session)

    # ---- Fault log subcommands ----
    sp = sub.add_parser("fault-read", help="read fault log from flash Sector 11")
    sp.add_argument("--slot", type=int, default=None,
                    help="read a specific slot (0–3); default: all slots")
    sp.add_argument("--raw", action="store_true",
                    help="dump raw hex instead of pretty-printing")
    sp.add_argument("--elf", default=str(_DEFAULT_ELF),
                    help=f"firmware ELF (default {_DEFAULT_ELF})")
    sp.add_argument("--swd-no-limit-packets", action="store_true",
                    help="disable single-in-flight USB mode")
    sp.set_defaults(func=cmd_fault_read)

    sp = sub.add_parser("fault-erase", help="erase flash Sector 11 (all fault records)")
    sp.add_argument("--confirm", action="store_true",
                    help="required: pass this flag to confirm the erase")
    sp.add_argument("--elf", default=str(_DEFAULT_ELF),
                    help=f"firmware ELF (default {_DEFAULT_ELF})")
    sp.add_argument("--swd-no-limit-packets", action="store_true",
                    help="disable single-in-flight USB mode")
    sp.set_defaults(func=cmd_fault_erase)

    return p


# -------------------------------------------------------------------------
# WiFi / subscribe subcommand implementations
# (placed after the parser definition so all imports see what they need)
# -------------------------------------------------------------------------

def _cmd_peekvar(args):
    """Read one DWARF variable once and print it.

    Equivalent to ``python -m ground_station.livewatch read <name>``, but
    names the verb 'peek' in the shell. Lives in its own function so the
    memory-address ``peek`` (probe mode) keeps a clean meaning.
    """
    # 'peekvar' is intentionally friendlier than 'read' for one-shot
    # telemetry-style invocations from shell scripts.
    args.names = [args.name]
    return cmd_read(args)


def cmd_clear(args):
    """Invoke preclear_fc_subscriptions() and print the stats dict."""
    # Lazy import keeps the module load order identical for users who only
    # use the read/watch subcommands.
    from .capture_preset import preclear_fc_subscriptions
    import json
    res = preclear_fc_subscriptions(
        host=args.host,
        port=args.port,
        rounds=args.rounds,
        drain_secs=args.drain_secs,
    )
    print(json.dumps(res, indent=2))
    return 0 if res.get("finished_clean", True) else 1


def cmd_probe_slots(args):
    """Send a stop per slot, watch the wire, report liveness.

    Limitation: API/subscribe.c declares s_streams[] as `static`, so DWARF
    can't expose it. The wire round-trip is the next-best signal.
    """
    import socket as _socket
    import time as _time
    from .stream import build_stream_request
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.settimeout(0.05)
    rows = []
    for slot in range(4):
        stop_req = build_stream_request([], divider=0, slot=slot, transport=1)
        sock.sendto(stop_req, (args.host, args.port))
        deadline = _time.monotonic() + args.per_slot_window
        seen = 0
        while _time.monotonic() < deadline:
            try:
                _data, _ = sock.recvfrom(2048)
                seen += 1
            except _socket.timeout:
                continue
            except OSError:
                break
        rows.append((slot, seen))
    sock.close()
    print(f"{'slot':<5} {'packets':>8}  status")
    print("-" * 28)
    for slot, seen in rows:
        status = "live (acks coming)" if seen > 0 else "silent (idle or FC offline)"
        print(f"{slot:<5} {seen:>8}  {status}")
    return 0


def _transport_args(sp):
    sp.add_argument("--transport", choices=("swd", "uart5", "wifi"), default="wifi")
    sp.add_argument("--uart5-port", help="manual UART5 COM port (overrides config.yaml)")
    sp.add_argument("--uart5-baud", type=int,
                    help="UART5 baud (overrides config.yaml; default 115200)")
    sp.add_argument("--swd-no-limit-packets", action="store_true",
                    help="disable single-in-flight USB mode for SWD reads "
                         "(use only for wired probes where throughput matters more than reliability)")
    return sp


def _manifest_args(sp):
    """A manifest is named, or built ad-hoc from --vars; --hz overrides either."""
    sp.add_argument("manifest", nargs="?", help="manifest name from manifests.yaml")
    sp.add_argument("--vars", nargs="+",
                    help="ad-hoc variable list (paths and/or group:<name>) instead of a manifest")
    sp.add_argument("--name", help="name for an ad-hoc manifest (used in the CSV filename)")
    sp.add_argument("--hz", type=float, default=None, help="override the manifest sample rate")
    return sp


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except LiveTransportError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    main()
