---
name: livewatch
description: >
  Full pyOCD capability layer for the research drone. Read or write any memory, flash,
  halt/step/resume/reset, breakpoints, watchpoints, RTT, SWO trace, GDB server — by name
  from DWARF or by address. Use when the user asks about target state, wants to read or
  write registers, break on a function, watch memory, stream RTT, capture SWO, start GDB,
  or directly manipulate the running firmware.
---

# livewatch — full probe access

Everything is unlocked. This is a research drone. The agent has unrestricted access to
`python -m ground_station.livewatch` and `ground_station.livewatch.probe`.

## Quick reference

```bash
# Always run first — catches stale ELF before it poisons any read
python -m ground_station.livewatch verify

# Offline (no hardware needed)
python -m ground_station.livewatch names --filter ekf       # symbol search
python -m ground_station.livewatch fields s_ekf            # struct members
python -m ground_station.livewatch groups                    # variable groups
python -m ground_station.livewatch manifests                # logging manifests
python -m ground_station.livewatch budget of_drift          # feasible sample rate

# DWARF-named reads
python -m ground_station.livewatch read s_ekf.x[3] DroneStatus.ARM_Status
python -m ground_station.livewatch watch group:ekf --hz 20 --secs 30
python -m ground_station.livewatch log of_drift --secs 60

# Core registers
python -m ground_station.livewatch registers --all                  # all ARM Cortex-M regs
python -m ground_station.livewatch registers pc msp primask        # specific regs
python -m ground_station.livewatch registers --halt                # halt then read
python -m ground_station.livewatch probe-info                       # state snapshot

# Execution control
python -m ground_station.livewatch halt                          # freeze core
python -m ground_station.livewatch resume                         # resume
python -m ground_station.livewatch step                           # one instruction
python -m ground_station.livewatch reset                          # system reset
python -m ground_station.livewatch reset --halt-after             # reset then halt

# Memory by address
python -m ground_station.livewatch peek 0x20000000               # 32-bit word
python -m ground_station.livewatch peek 0x20000000 --size byte    # byte
python -m ground_station.livewatch peek 0x20000000 --size dword   # 64-bit
python -m ground_station.livewatch peek 0x20000000 --size block -n 128  # block read
python -m ground_station.livewatch dump 0x20000000 256            # hexdump
python -m ground_station.livewatch poke 0x20000000 0x12345678      # write word

# Flash
python -m ground_station.livewatch flash-read 0x08000000 256     # read flash
python -m ground_station.livewatch flash-write firmware.bin --base 0x08000000
python -m ground_station.livewatch flash-erase --address 0x08000000 --size 0x1000
python -m ground_station.livewatch flash-write OBJ/JX_FLY.axf     # flash ELF

# Breakpoints
python -m ground_station.livewatch bp 0x08001234                   # hardware BP
python -m ground_station.livewatch bp 0x08001234 --sw              # software BP
python -m ground_station.livewatch bp-list
python -m ground_station.livewatch bp-remove 0x08001234

# Watchpoints
python -m ground_station.livewatch wp 0x20000000                  # watch access
python -m ground_station.livewatch wp 0x20000000 --type write      # watch writes only
python -m ground_station.livewatch wp 0x20000000 --type read -s 8 # watch 8B reads
python -m ground_station.livewatch wp-remove 0x20000000

# RTT (needs SEGGER_RTT linked in firmware)
python -m ground_station.livewatch rtt-list                       # discover channels
python -m ground_station.livewatch rtt-read                        # read channel 0
python -m ground_station.livewatch rtt-read --channel 1 --timeout 5
python -m ground_station.livewatch rtt-write "ping\n"              # host -> target
python -m ground_station.livewatch rtt-telnet                      # telnet bridge on :20294

# SWO trace (needs ITM configured on MCU side)
python -m ground_station.livewatch swo-read                       # 1 MHz default
python -m ground_station.livewatch swo-read --baud 2000000 --timeout 10

# GDB server
python -m ground_station.livewatch gdbserver                      # :3333
python -m ground_station.livewatch gdbserver --port 3334
# then in gdb:  target remote localhost:3334

# Probe enumeration
python -m ground_station.livewatch probes                          # list all probes
```

## DWARF name syntax

`group:<name>` expands from `registry.yaml`. Array elements: `s_ekf.x[3]`. Struct fields:
`mrac_state.roll.What[0]`. Path must resolve to a symbol in `OBJ/JX_FLY.axf`.

## Programmatic API

```python
from ground_station.livewatch import ProbeSession

with ProbeSession() as probe:
    probe.halt()
    regs = probe.registers_read()
    print(probe.registers_dump())

    # Write a value
    probe.write_memory(0x20000000, 0xDEADBEEF, size=32)

    # Read back
    v = probe.read_memory(0x20000000, size=32)
    print(f"0x{v:08X}")

    # Breakpoint
    probe.bp_set(0x08001234)   # hardware
    probe.resume()

    # RTT
    probe.rtt_start()
    caps = probe.rtt_read_channel(0, timeout_s=2.0)
    for cap in caps:
        print(cap.data.decode())

    # Flash
    probe.flash_elf("OBJ/JX_FLY.axf")

    # Target info
    print(probe.info())
```

## Performance notes

- Wireless CMSIS-DAP is bandwidth-limited (~33 KB/s). Read adjacent fields in one call.
- SWD `read_memory_block8` is optimal; individual `read8` calls degrade badly.
- One process at a time holds the probe.
- RTT and SWO require firmware-side ITM/RTT wiring (future firmware sessions).
