# Research and design evidence

Date: 2026-09-16

This note separates three kinds of information: **project measurements**, **primary
vendor/standards documentation**, and **claims visible in the attached MicoAir
slides**. The slides are treated as product evidence, not as implementation
instructions. Any unverified module claim remains a test item.

## Hardware facts and implications

### MicoAir module

The attached material identifies the module family as an ESP32-C5 based dual-band
Wi-Fi 6 serial-to-UDP transparent transport. It advertises 2.4/5 GHz, AP and STA
modes, UDP serial transparency, BLE serial transparency, a 3.3 V LVTTL UART, USB-C
CH340 configuration, 5 V supply, and selectable transparent UART rates including
57,600, 115,200, 230,400, 460,800, and 921,600 baud. It gives indicative ranges of
100 m in AP mode, 500 m in STA mode through a high-power router, and 10 m for BLE.
These are vendor/environment claims; the project’s measured throughput, loss, RSSI,
latency, and range must remain authoritative for our installation.

Primary references:

- Espressif ESP32-C5 product page: https://www.espressif.com/en/products/socs/esp32-c5
- ESP32-C5 hardware reference: https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/hw-reference/index.html
- ESP-IDF Wi-Fi API: https://docs.espressif.com/projects/esp-idf/en/latest/esp32c5/api-reference/network/esp_wifi.html

Design consequences:

- Keep the FC UART framing independent of Wi-Fi packet boundaries. UDP is a
  datagram transport, but transparent serial bridges may split or coalesce data.
- Add sequence numbers, CRC, timestamps/ticks, and loss/latency counters at the FC
  protocol layer; never infer health from socket receive success alone.
- Treat AP mode as a point-to-point bench/field mode and STA mode as a multi-device
  laboratory mode. Do not assume that switching modes preserves IP, routing, or
  downlink source behavior.
- Use the USB/CH340 interface only for module configuration and diagnostics. Keep it
  out of the flight-control data path.
- The present project’s 921600-baud configuration and measured Wi-Fi behavior are
  the working baseline. The nominal ESP32-C5 Wi-Fi capability does not guarantee the
  bridge firmware’s transparent-serial throughput.

### STM32F407 flight controller

The firmware project targets the STM32F407 family. The official STM32F407 datasheet
and reference manual should be treated as the electrical/peripheral authority:

- Datasheet: https://www.st.com/resource/en/datasheet/stm32f407zg.pdf
- Reference manual RM0090: https://www.st.com/resource/en/reference_manual/dm00031020.pdf
- CMSIS device headers in this repository are also part of the build contract.

Relevant architectural constraints for this project are the Cortex-M4F real-time
execution model, finite on-chip Flash/SRAM, DMA and interrupt interactions, and the
need to preserve deterministic control-task timing. New registries and telemetry
must therefore use static storage, bounded frame sizes, compile-time limits, and
explicit diagnostic build profiles. Do not introduce heap-dependent plugin loading,
large printf paths, or blocking network logic into control tasks.

The F4’s DMA/USART behavior makes a producer/queue/transport split essential. A
telemetry encoder must publish snapshots to a bounded buffer; a transport task may
drop or decimate diagnostic data under pressure, but must never block the
stabilizer or motor path.

### Wireless SWD debugger

The project’s livewatch documentation and flashtool are the operational authority
for this probe. Existing measurements identify the wireless CMSIS-DAP path as much
slower than the MicoAir data plane and vulnerable to contention. Therefore:

- Use SWD for build/flash, ELF/DWARF verification, low-rate probe reads, breakpoints,
  watchpoints, and recovery.
- Do not use SWD as the normal high-rate telemetry or command plane.
- Ensure only one probe client owns the adapter at a time.
- Preserve the existing stale-ELF/build-identity guards; an ELF with moved symbols
  must never be used to interpret a different running image.
- Dashboard-triggered flash remains a controlled engineering operation, separate
  from normal flight commands.

## Mature patterns worth adopting

### PX4 uORB: typed asynchronous topic separation

PX4’s uORB pattern separates publishers and subscribers through typed topics,
allowing modules to evolve without direct call coupling. It is a useful model for
our firmware registry and controller interfaces, while remaining too dynamic/heavy
to copy literally into this ARMCC project.

Reference: https://docs.px4.io/main/en/middleware/uorb.html

Adopt the principle: typed messages, ownership, update policy, and decoupled
consumers. Implement it with static C descriptors, bounded queues, and explicit
copy/snapshot rules.

### MAVLink: explicit parameter/command semantics

MAVLink’s parameter protocol demonstrates the value of named parameters, metadata,
acknowledgements, and explicit set/read operations. MAVLink serialization also
demonstrates versioned framing, incompatibility flags, CRC, and sequence numbers.

References:

- https://mavlink.io/en/services/parameter.html
- https://mavlink.io/en/guide/serialization.html

Adopt these semantics without making MAVLink the firmware’s primary protocol:
transaction IDs, ACK/rejection reasons, typed values, metadata, and version
negotiation. An eventual MAVLink adapter remains possible.

### Foxglove and MCAP-style observability

Foxglove’s visualization model supports live data sources, playback, custom panels,
performance inspection, and extensions. It is a good reference for our separation
between a data service, a replayable log, and pluggable visualization panels.

Reference: https://docs.foxglove.dev/docs/visualization

Adopt the pattern, not necessarily the product: stable topic/schema identity,
recordable sessions, deterministic replay, panel plugins, and visible data-pipeline
latency/throughput.

### PlotJuggler-style analysis

PlotJuggler is a strong reference for high-rate time-series inspection and should
remain an optional external analysis surface rather than becoming the control UI.

Reference: https://plotjuggler.io/

### OpenTelemetry-style event correlation

OpenTelemetry’s logs/metrics/traces separation is applicable to the ground station:
telemetry is metrics-like, command/event history is log-like, and a single
experiment/session ID provides trace-like correlation.

Reference: https://opentelemetry.io/docs/concepts/observability-primer/

Adopt consistent IDs and timestamps across raw frames, decoded samples, commands,
events, builds, flash operations, and analysis artifacts.

## Recommended communication patterns

1. **FC data plane:** binary, typed, sequence-numbered, CRC-protected frames over
   MicoAir UDP; telemetry is publish/subscribe and bounded by negotiated budget.
2. **FC control plane:** transactional commands with ACK/rejection/applied events;
   safety checks execute on the FC, not only in the dashboard.
3. **Host service plane:** one process owns the Wi-Fi socket, decodes frames, logs
   raw and typed data, and exposes a local API/WebSocket. UI plugins never open the
   FC socket directly.
4. **Probe plane:** a separate single-owner service for build, flash, verify, and
   livewatch. It must expose operation logs and build identity to the dashboard.
5. **Replay plane:** the same decoder and state reducer consume live or recorded
   frames, so every UI and agent workflow can be tested without the drone.

## Tests to add before later implementation

- Wi-Fi AP/STA discovery, reconnect, source-port/downlink routing, and module reset
  tests.
- UDP fragmentation/coalescing, packet loss, reorder, duplicate, and stale-session
  tests using recorded and synthetic streams.
- UART baud/rate sweep at the actual FC and module settings.
- Command transaction timeout, duplicate, rejection, and reconnect tests.
- Telemetry budget tests with simultaneous legacy and subscribed streams.
- SWD ownership, stale ELF, interrupted flash, retry, and post-flash identity tests.
- Resource instrumentation overhead tests for each diagnostic build profile.
- Active/shadow controller equivalence and safe-abort tests.

## Strategic conclusions

- The MicoAir is sufficient as the primary operational link, but its bridge firmware
  is the system limit until measured; Wi-Fi 6 marketing rates are not UART-to-UDP
  guarantees.
- The custom protocol should evolve toward MAVLink-like semantics and PX4-like
  typed decoupling, while retaining compact static implementation for STM32F4.
- The first firmware milestone remains the registry/capability/transaction/event
  foundation. UI work before those contracts would recreate the old coupling.
- AP and STA should be first-class link profiles, and every session must record the
  active profile and measured network conditions.

