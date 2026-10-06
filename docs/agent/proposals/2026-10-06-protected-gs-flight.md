# Proposal: protected-region follow-ups for the stick-free GS flight (2026-10-06)

Propose-only: these touch `API/wfb_glue.c` / `wfb_prim.c` (protected). The operator decides; nothing here is applied.

| # | Where | Today (read from source at 42ffacc) | Proposed | Why |
|---|---|---|---|---|
| 1 | `wfb_glue_land` / `wfb_prim_land` | LAND before `airborne` returns WFB_ERR_STATE; f01 saw LAND ignored in CLIMB. The runner resends LAND once prim is HOVER (`campaign_runner.py` land loop), so the drone first climbs to hover_z | accept LAND in the TAKEOFF primitive: stop the climb, go straight to the descend | an abort during the climb (operator land, GS abort) lands from the current height instead of hover_z |
| 2 | `wfb_glue_cmd_prim` TAKEOFF gate | refuses on `s_wfb.takeover`; GS gate b2cd094 also refuses on `status.rc_authority` 1 -> 0 | confirm on the bench that a fast stick move during the GS idle sets `s_wfb.takeover` (one test in `tests/firmware_host/test_wfb_glue.c`) | two independent refusals; today only the GS one has a test |
| 3 | `send_data.c` CMD 0x0E idx 1 val 0 (not protected) | idle-off keeps the SDK stick authority (virtual THR -1) until a takeover or disarm | release the authority there in FlyMode_SDK | nothing sends idle-off today; only matters if a future runner does |
