# S2 — Identity, capabilities, and static registry

## Outcome

S2 is complete. The firmware now exposes a stamped build identity, capability
discovery, and a generated static registry. The host client validates the
identity and registry digest over the MicoAir UDP path. The Keil download path
now issues an explicit `SYSRESETREQ` and confirms `RUNNING` before reporting
success; this resolves the observed post-flash state where the MCU remained
between reset and C runtime startup and the ESC beeps continued.

## Implementation

- Source of truth: `registry/platform_registry.yaml`.
- Generated firmware tables: `firmware/platform_registry_gen.{h,c}`.
- Generated host/documentation views: `ground_station/generated/platform_registry.json`
  and `docs/dashboard-platform/generated/platform-registry.md`.
- Firmware discovery handlers: `firmware/platform_registry.{h,c}`, wired through
  `BSP/usart5.c`, `API/subscribe.c`, and `USER/main.c`.
- Host parser/client: `ground_station/platform/discovery.py`.
- Build identity sidecar: `OBJ/.build_identity.json`. ARMCC AXF files are not
  ELF, so identity recovery uses an ELF initializer when available, then the
  sidecar, then the legacy counter fallback.
- Artifact custody snapshots include the sidecar and remove it when restoring
  artifacts from a pre-stamping build.
- `USER/JX_FLY.uvprojx` has `UpdateFlashBeforeDebugging=1`.

## Verification evidence

Build and tests:

```text
python -m pytest ground_station/flashtool/tests ground_station/platform/tests -q
99 passed

python -m ground_station.flashtool build
UV4 exit 0; build OK; Keil AXF/HEX/MAP generated
```

The final migration build allocated identity:

```text
magic       0xB10DCAFE
counter     7
epoch       1789577020
fingerprint 0x2D970CA7
```

Keil download and post-download recovery:

```text
Erase Done. Programming Done. Verify OK.
post-download SYSRESETREQ: State.RUNNING
```

Live SWD reads after flashing:

```text
DroneStatus.ARM_Status       0
system_monitor.USART2_task_cnt 13714 (advancing)
build_id                     B10DCAFE, 7, 1789577020, 2D970CA7
build identity MATCH
```

MicoAir discovery (`192.168.4.1:14550`) returned:

```text
protocol=14 format=1 schema=1 registry=1
hardware_id=1031 capabilities=0xFF
registry_crc32=0x9F32E2EA descriptor_count=39
build_id=(0xB10DCAFE, 7, 1789577020, 0x2D970CA7)
registry digest counts:
variable=5 enum=2 parameter=4 command=7 telemetry=4
event=2 plugin=4 task=7 resource=4
```

The live CRC, descriptor count, per-kind counts, and build identity match the
generated registry and sidecar. The custody/rollback unit suite passes,
including restoration of a pre-stamping artifact set.

## Risks and next gate

The reset cause is currently reported as the raw STM32 RCC CSR value; later
sessions should map it to named flags without changing the discovery wire
shape. Existing S1 contract-drift failures remain documented and are not
silently reclassified by S2. S3 may now add transactional command and event
semantics on top of this identity and registry foundation.
