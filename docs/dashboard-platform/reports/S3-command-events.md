# S3 — Transactional commands and events

The first migration slice adds a versioned transaction envelope without
changing the legacy `0xCC 0xDD` command path. The envelope is deliberately
compatible with the existing command semantics: command ID, index, and the
float32 value remain the application payload, while the transport adds a
version, flags, transaction ID, and length.

## Implemented

- `ground_station/platform/transactions.py` encodes and validates command
  envelopes (`0xCC 0xDF`) and structured result frames (`0x30` ACK, `0x31`
  rejected, `0x32` applied).
- Result frames carry a transaction ID, command identity, rejection reason,
  and bounded UTF-8 detail text.
- `TransactionLedger` provides bounded duplicate detection for retries.
- `WifiBridge.send_transaction()` allocates nonzero transaction IDs and sends
  the new envelope from the MicoAir command socket. Existing
  `send_command()` callers continue to use the legacy path.
- `firmware/command_protocol.{h,c}` provides the C90 parser and checksum
  primitive. Keil includes the source in the `firmware` project group.

## Verification

```text
gcc -std=c90 -Wall -Wextra -Werror -Ifirmware -c firmware/command_protocol.c
python -m pytest ground_station/platform/tests ground_station/comm/tests/test_slot0_layout.py -q
19 passed
python -m ground_station.flashtool build
UV4 exit 0; build OK
python -m pytest ground_station/flashtool/tests ground_station/platform/tests ground_station/comm/tests/test_slot0_layout.py -q
117 passed
```

The S3 image was rebuilt and flashed over the powered target. UV4 reported
`Erase Done. Programming Done. Verify OK.` and the guarded flashtool then issued
`post-download SYSRESETREQ`, leaving the core in `State.RUNNING`. The explicit
reset was also added to `rebuild_and_flash.flash()` so that this path cannot leave
the part waiting for a manual Debug/Run click. The ESC startup beeps stopped after
the reset.

Live MicoAir UDP evidence (port 14550) after the corrected image was running:

- Transaction `0x4245`, command `0xFE`: one `0x31` rejected result, reason
  `UNKNOWN_COMMAND`, detail `command rejected`; the result CRC was `0x8D`,
  matching the host golden vector.
- Transaction `0x5001`, command `0x12`, index `0`, value `0.0`: `0x30 ACK`
  (`queued`) followed by `0x32 APPLIED` (`applied`).
- Replaying the exact `0x5001` frame produced one `0x32 APPLIED` result with
  detail `duplicate`; no second command execution was observed.
- Transaction `0x5002`, command `0x06`: one `0x31 REJECTED` result with reason
  `SAFETY_INTERLOCK` while the aircraft remained disarmed.
- The live socket received 156, 148, and 156 telemetry datagrams during the
  first, duplicate, and safety-rejection windows respectively, showing the
  scheduler and USART3 data path continued while events were emitted.

The host splitter now recognizes result frame types `0x30..0x32` and exposes
`WifiBridge.poll_transaction_result()`. `parse_result_parts()` covers the
`pop_frame()` `(frame_type, version, payload)` contract and has a golden-vector
test.

## S4 entry

S3 gate passed. S4 can begin with typed telemetry and transport hardening.
