# S1 - Baseline and contract audit

## Scope

This report audits the current firmware and ground-station command and telemetry paths before dashboard implementation. The source of truth is the checked-out source tree and existing host tests. No S2 implementation was started.

The audit classifies paths as `verified` (source plus deterministic host evidence), `unverified` (implemented or historically measured but not revalidated in this session), `deprecated` (still documented or present but no longer the intended path), or `unsafe` (a mutating/safety-sensitive path that cannot be operated unattended under the current protocol contract).

## Changes

- Added the deterministic synthetic replay fixture at `docs/dashboard-platform/fixtures/S1-synthetic-replay.json`.
- Added this baseline report.
- Updated `docs/dashboard-platform/STATE.md` with the gate result, evidence, and blockers.

No firmware, host runtime, generated build artifact, or existing README changes were made.

## Protocol matrix

| Path | Wire contract and owner | CRC | Classification | Evidence and limitation |
|---|---|---|---|---|
| Legacy telemetry envelope | `0xAA 0xBB type len_be16 basis_count payload`; serialized by `TASK/send_data.c`, transported through UART4 DMA/USART3 forwarding and decoded by `ground_station/comm/serial_bridge.py` | XOR CRC8 for frames `0x01`-`0x05`; Frame `0x06` uses CRC16-CCITT/XModem | verified in host parser; hardware unverified | Host schema tests and source inspection pass. No fresh target capture. |
| Frame A `0x01` | Status and eight float channels, nominal 100 Hz in the legacy path | XOR CRC8 | unverified rate; verified layout | `frame_simulator.py` and protocol schema cover the layout; rate is source/document evidence only. |
| Frame B `0x02` | MRAC/PID/path payload, basis-count dependent | XOR CRC8 | unverified rate; verified layout | Host simulator and schema formulas cover known basis counts. |
| SysID `0x03` | Single-axis excitation record when `id_frame_on` is set | XOR CRC8 | unverified | Source path and host decoder exist; no fresh SysID run. |
| Bench `0x04` | Motor test sample, counter/motor/CCR/voltage/active/RPM fields | XOR CRC8 | unverified | Source and simulator contract exist; no hardware bench capture. |
| OF calibration `0x05` | Optical-flow calibration/fusion record when `of_frame_on` is set | XOR CRC8 | unverified | Source path exists; no fresh target observation. |
| Extended telemetry `0x06` | Extended attitude/telemetry frame | CRC16-CCITT/XModem | unverified | Host schema pins CRC family; current ELF/schema mismatch prevents complete symbol confidence. |
| One-shot subscribe `0x20` | Host sends `0xCC 0xDE 0x20` tuples `(address LE32,size LE16)`; firmware validates SRAM/CCM and replies `0x07` or `0x7F` | XOR CRC8 | verified parser contract; transport unverified | C parser and host tests cover validation. UART5 staging is 256 B; no hardware round trip in this session. |
| Streaming subscribe request `0x21` | `0xCC 0xDE 0x21`, divider/transport/slot, up to 62 ranges, CRC8 | XOR CRC8 | verified parser contract; ingress transport unverified | C/source and host layout tests cover limits. Current code accepts USART3, while stale comments/docs still say UART5-only. |
| Streaming schema `0x08` | Echoes accepted ranges, divider, transport, slot, total bytes | XOR CRC8 | verified builder; hardware unverified | `Subscribe_BuildSchema`, host decoder, and tests cover layout. |
| Streaming data `0x09`-`0x0c` | Slot-coded frame, sequence, source timestamp, packed values | CRC16-CCITT/XModem | verified builder/decoder; hardware unverified | Host stream tests cover sequence gaps, CRC, width, and slot decode. |
| Subscribe error `0x7f` | Error text plus requested count echo | XOR CRC8 | verified builder/decoder; hardware unverified | Host parser tests cover error propagation. |
| `0xCC 0xDD` command ingress | 9 bytes: sync, command ID, index, float32 LE, XOR CRC8; queue is shared and 16 entries | XOR CRC8 | verified syntax; unsafe semantics | `BSP/usart4.c` and `BSP/usart5.c` parse it; no ACK, transaction ID, applied event, or duplicate semantics. |
| USART3 command ingress | USART3 IDLE mailbox calls the shared parser for `0xCC 0xDD` and currently `0xCC 0xDE` | As above | unverified hardware; documentation conflict | Source routes subscribe replies through USART3 TX ring. No hardware round trip in this session. |
| UART5/CMSIS-DAP ingress | UART5 command path exists; subscribe path is compile-time gated and defaults disabled in current source | As above | deprecated/default-disabled for subscribe | Host and header comments still describe it as primary subscribe control path. |
| UART4 legacy ingress | UART4 DMA mailbox parses the same `0xCC 0xDD` grammar into the shared queue | XOR CRC8 | deprecated/legacy | It remains implemented at 115200 but is not the preferred Wi-Fi operational plane. |

Transport ownership is therefore split: USART3 is the current operational radio command/data path and owns its TX ring while a USART3 stream is active; UART5 is the engineering/probe path and can service short polling requests; UART4 remains a legacy command/telemetry path. The source tree has not completed the documentation migration for this split.

## Command matrix

Every command below uses the 9-byte `0xCC 0xDD` frame. A command is marked `unsafe` when it changes control authority, flight state, calibration, motor output, or an experiment without a firmware acknowledgement/rejection event. Invalid indexes or values are generally silently ignored.

| ID | Indices and checks | Side effect | Classification |
|---|---|---|---|
| `0x01` | `idx=axis*3+gain`, axis 0..6, gain 0..2, value 0..200 | PID Kp/Ki/Kd update | unsafe |
| `0x02` | high nibble axis 0..3, low nibble basis `< MAX_NUM_BASIS`, value `>0` | MRAC gamma element | unsafe |
| `0x03` | idx 0..3 mixer, 4..7 `u_max`, 8/9 throttle min/max in 0..1 | Mixer and throttle limits | unsafe |
| `0x04` | idx 0 abort/stop; idx 1 recover SDK | Flight FSM event, path abort, authority change | unsafe |
| `0x05` | MRAC axis/basis as 0x02, value `>=0` | MRAC `What_limit` | unsafe |
| `0x06` | idx 0..3, SDK mode only; clamp value to [-1,1] | Virtual RC injection | unsafe |
| `0x07` | idx 0 | Bench mode flag | unsafe |
| `0x08` | MRAC axis/basis as 0x02, value `>=0` | MRAC `What_tol` | unsafe |
| `0x09` | idx0 horizontal (0.05,20), idx1 vertical (0.05,10), idx2/3 pitch/roll [3,60] | Ground-station safety limits | unsafe |
| `0x0a` | SDK only; idx 0..4 | TWC target and execute | unsafe |
| `0x0b` | SDK only; idx 0..7 | Sinusoid path parameters/start/stop | unsafe |
| `0x0c` | SDK only; idx 0..6 | Circle path parameters/start/stop | unsafe |
| `0x0d` | idx 0 | Abort paths, neutral sticks, dangerous stop | unsafe |
| `0x0e` | idx 0, nonzero requests arm/authority; zero relinquishes PC authority | SDK arm request / RC authority | unsafe |
| `0x0f` | idx 0..12 MRAC flags; 100 legacy, 101 mixed, 102 subscribe-only | Runtime flags and telemetry mode | unsafe for mode/authority; otherwise unsafe due to silent result |
| `0x10` | idx 0 | Reset optical-flow/world origin | unsafe |
| `0x11` | SDK only; idx 0..7 | Figure-8 path parameters/start/stop | unsafe |
| `0x12` | idx 0, clamp negative spacing to zero | Waypoint spacing and reset | unsafe |
| `0x13` | idx 0, selector rounded/clamped to 0..2 | Reference model switch and state snap | unsafe |
| `0x14` | idx 0..7; start/abort at 6, geofence at 7 | SysID excitation and geofence | unsafe |
| `0x15` | idx 0 enable, idx 1 cutoff | Gyro filter | unsafe |
| `0x16` | idx0 heartbeat, idx1 motor 0..4, idx2 CCR clamped [2000,4000] | Disarmed motor bench output | unsafe |
| `0x17` | idx 0 | Request OF bias capture | unsafe |
| `0x18` | idx 0, only `GROUND_IDLE` and `DisArmed` | Force recalibration/reset EKF | unsafe |
| `0x1e` | idx0 mode rounded to FIXED/EMA/EKF; idx1 freeze >=0.5 | OF bias estimator mode/freeze | unsafe |
| `0x20` extended read | Tuple list `(address,size)`, max 32 tuples; SRAM/CCM allowlist and alignment checks | Read-only one-shot subscribe reply `0x07` or error `0x7f` | verified parser; transport unverified |
| `0x21` stream subscribe | Divider, transport, slot, up to 62 validated ranges; divider 0 stops the selected slot | Creates/replaces/stops one of 4 stream slots and emits `0x08` schema | verified parser; transport unverified |
| Other IDs | No handler | Silently ignored after CRC-valid enqueue | deprecated/unsafe to rely on |

The command queue has a finite capacity of 15 usable entries (16-slot ring with one empty sentinel); overflow increments `gs_cmd_drop_count` but is not reported to the host. CRC-valid commands can therefore be lost without notification.

## Firmware ownership and resource inventory

| Resource | Owner | Static capacity or cadence | Audit note |
|---|---|---|---|
| USART3 RX | `BSP/usart3.c`, `TASK/stm32f4xx_it.c` | DMA/mailbox 256 B | Operational command and stream ingress; historical comments are partly stale. |
| USART3 TX | `BSP/usart3.c` | DMA1 Stream 3, 4096 B ring | Stream owns link while active; `UA3TxFrames/Drops/Peak` instrumentation exists. |
| UART5 RX/TX | `BSP/usart5.c` | DMA1 Stream 0/7, 256 B RX/staging | Engineering path; subscribe ingress compile-time disabled by default. |
| UART4 RX/TX | `BSP/usart4.c` | DMA1 Stream 2/4, 128 B RX; 115200 | Legacy command and telemetry path; queue storage is defined here. |
| Command queue | `BSP/usart4.c` | 16 entries of `{id,index,float}` | Shared by UART4, UART5, USART3; drops are counted, not acknowledged. |
| Stream state | `API/subscribe.c` | 4 slots, up to 62 ranges, 1024 value bytes | Up to 4 frames per Send_Task cycle; aggregate budget guard. |
| FreeRTOS heap | `FreeRTOS/include/FreeRTOSConfig.h` | Dynamic allocation, 20 KiB | Malloc-failed hook halts after fault capture. |
| RTOS tick | FreeRTOS | 1000 Hz | Runtime stats disabled; stack HWM API enabled. |
| Tasks | `USER/main.c` | System monitor 1 Hz; IMU deal/sample 1 kHz; stabilizer/autofly 200 Hz; remoter 100 Hz; Send mixed behavior | Send is ~80 Hz in mixed legacy mode and nominal 200 Hz in subscribe-only source path; no fresh target measurement. |
| Stack fault instrumentation | `USER/main.c` | Overflow marker `0xCAFEBABE`, task-name CRC | Configured, but no target dump was collected. |

Existing `OBJ/JX_FLY.map` metadata reports STM32F407ZGTx, 88,652 B ROM and 122,376 B total RW/ZI usage, with a 128 KiB RAM execution region. These values are historical artifact metadata because Keil tools were unavailable and no rebuild was performed.

## Inconsistencies and migration risks

1. `docs/telemetry-protocol.md`, `API/subscribe.h`, `BSP/usart5.h`, and `ground_station/livewatch/stream.py` retain UART5-only subscribe language, while current ingress code accepts `0xCC 0xDE` on USART3 and routes replies to the request transport.
2. Host stream arithmetic uses nominal `SEND_TASK_HZ=200` for conservative budget checks, while firmware `SUBSCRIBE_SEND_TASK_HZ` is 80 for mixed mode. The measured cadence is not a universal constant because subscribe-only and mixed modes differ.
3. The checked-in ELF/DWARF is stale relative to current host manifests (`imu_data.acc_x` cannot be resolved), so symbol-based stream plans are not release evidence.
4. The boot/default stream can contain complete structs and 46 values while the `_base` manifest names only 12 fields; schema negotiation is required before interpreting a stream.
5. Historical docs cite older slot/range limits and transport assumptions. Current source allows 4 slots and 62 ranges, but UART5 staging still limits large request frames.
6. The command protocol has CRC but no transaction ID, ACK, rejection reason, applied event, or idempotence contract. Silent ignore and queue overflow make dashboard command state unverifiable.
7. Generated OBJ/ELF artifacts may not correspond to the current source tree. A fresh Keil rebuild, flash identity check, and target resource dump remain outstanding.

## Firmware build/flash identity

No fresh build, flash, or target ELF verification was possible. `UV4`, `UV5`, `fromelf`, `armcc`, `make`, and `cmake` were not available on PATH. Existing `OBJ/JX_FLY.build_log.htm` records a historical Keil build with 0 errors and 83 warnings; it is not treated as current validation.

## Tests and hardware evidence

Executed in this session:

```text
python -m pytest ground_station/flashtool/tests -q
93 passed

python -m pytest ground_station/livewatch/tests -q
129 passed, 3 failed
```

The three livewatch failures are contract drift, not clean S1 validation: stale ELF schema (`imu_data.acc_x` missing), host/firmware `SUBSCRIBE_SEND_TASK_HZ` mismatch (host test expects 200, firmware defines 80), and a C harness compile failure because `Subscribe_RxTransport` is undeclared in the harness build.

The comm test package did not collect: `test_protocol_schema.py` imports `scripts.validate_protocol_schema`, but no importable `scripts` package is present from the repository test invocation. This is recorded as an infrastructure/test-path failure.

No hardware, Keil rebuild, flash, Wi-Fi round trip, command ACK test, multi-slot loss test, or binary capture was performed. The fixture in `fixtures/S1-synthetic-replay.json` is synthetic and deterministic; it is not a hardware capture.

## Files/artifacts produced

- `docs/dashboard-platform/reports/S1-baseline.md`
- `docs/dashboard-platform/fixtures/S1-synthetic-replay.json`
- `docs/dashboard-platform/STATE.md` update

## Risks and unresolved questions

- S2 must not begin until the source/documentation transport split is reconciled and all command/telemetry paths have an explicit owner and classification.
- A hardware operator must capture a fresh USART3 subscribe handshake, schema, multi-slot stream, sequence/loss counters, and command ingress trace.
- The command plane requires S3 transactional semantics before dashboard controls can claim applied state.
- A fresh firmware build must generate a matching ELF/DWARF and resource report before symbol-backed manifests are trusted.

## Rollback

Remove the two new S1 artifacts and revert only the S1 report/STATE changes. Existing modified OBJ artifacts, `README.md`, and other pre-existing worktree changes must remain untouched.

## Next session recommendation

Leave the project at the S1 gate. Resolve the documented blockers and perform target validation first; then start S2 (identity/capability/registry foundation). Do not implement dashboard controls against the current unacknowledged command plane.
