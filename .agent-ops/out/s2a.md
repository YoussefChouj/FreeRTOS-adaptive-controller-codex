STATUS: done (flight-critical tier-0 edits, operator permission granted). Nothing flashed, uVision GUI untouched, 8081 untouched.

Base: HEAD 6eed617. Branch worktree-agent-ada7aecefc04ec1b7.

## Files changed

- `API/mrac_variant.h`: `MRAC_CAPACITY` is now `#ifndef`-guarded, default `MRAC_N_FEATURES`.
- `API/mrac.h`: `MRAC_TELEM_WINDOW` (6) + `MRAC_Assert_Window`; `extern const uint8_t mrac_n_features`; `MRAC_CycSet_t`, `MRAC_Cyc_t`, `extern MRAC_Cyc_t mrac_cyc`. The existing `MRAC_Assert_Features` (features <= capacity) and `MRAC_Assert_Basis` (`MAX_NUM_BASIS <= 16`) are kept.
- `API/mrac.c`: feature loops run over `MRAC_N_FEATURES`; `MRAC_CCM` tag on the adaptive state; DWT cycle stamps (`MRAC_CYC_NOW`, `MRAC_CycEnd`, `mrac_cyc`); `mrac_n_features`.
- `TASK/send_data.c`: header byte [5] and the Frame B Theta payload use `MRAC_TELEM_WINDOW`; three compile-time asserts. GBK/CRLF file, edited with latin-1 byte scripts only, still 2029/2029 CRLF.
- `USER/JX_FLY.sct` (new, CRLF): scatter file with the CCM region.
- `USER/JX_FLY.uvprojx`: `<umfTarg>0</umfTarg>` and `<ScatterFile>.\JX_FLY.sct</ScatterFile>`, nothing else.
- `API/tests/test_mrac_equiv.c`, `API/tests/run_mrac_equiv.py`: test-side support for `--define MRAC_CAPACITY=16` (see A).
- Not touched: `API/pid.c`, `BSP/rpm.c`, `TASK/StabilizerTask.c`, `.gitignore`, `ground_station/`, `OBJ/*` (rebuilt by Keil, not committed). `.gitignore` shows modified in the worktree from a skipper hook, not by this work, and is not committed.

## Decisions

### A. Latent OOB bug
`MRAC_CAPACITY` used to be hard-wired to 6, and `MAX_NUM_BASIS` (= capacity) sized the storage while the loops ran to `MAX_NUM_BASIS`. With a capacity above the feature count those loops would read Phi/Theta entries no generator writes and add extra terms. Feature loops now run to `MRAC_N_FEATURES`: Phi_sq, grad, sigma-prior grad, ProjectGradient, Theta update, `raw_u_ad`, `sum_w2`. Storage-sized loops (`MRAC_Reset`, `MRAC_SetPrior`/`GetPrior`, the `Theta_prior[AXES][MAX_NUM_BASIS]` array) stay at `MAX_NUM_BASIS`, because they must cover all storage. No sum was reordered; at the default the trip counts are identical (6), so the float sequence is identical.
The `param` command bound (`elem < MAX_NUM_BASIS`, send_data.c ~1497) stays: it protects the array and is correct for any capacity. Its 4-bit element index caps capacity at 16, which is why the `MAX_NUM_BASIS <= 16` assert stays.
Test side: `test_mrac_equiv.c` dump loops use `N_ACTIVE` (= `MRAC_N_FEATURES` in the new tree, `MAX_NUM_BASIS` in the base tree that predates the symbol), so a wider capacity only adds unused storage and the dumps stay comparable. `run_mrac_equiv.py` got `--define NAME=VAL`, applied to the new-tree builds only (the base tree predates `MRAC_CAPACITY`). `--self-test` is unchanged.

### B. Telemetry window
`MRAC_TELEM_WINDOW = 6`, fixed independent of capacity so the frame layout does not move with `MRAC_CAPACITY`. Asserts in send_data.c (`typedef char NAME[cond ? 1 : -1]`, the armcc C99 idiom):
- `FrameB_Fits_Buf`: `6 + (4*(W+2)+36)*4 + 26 + 1 <= sizeof(Buf_Telemetry_UART4)` (512).
- `FrameB_Float_Count_U8`: `4*(W+2)+36 <= 255` (`total_floats` is `uint8_t`).
- `MRAC_Assert_Window` in mrac.h: `W <= MRAC_N_FEATURES`.
Negative tests (compile-time): W=40 and W=60 fail the asserts; W=6 and W=16 pass. Capacity 4 and 17 fail; 6 and 16 pass.
Frame B is 305 bytes at W=6 (total_floats 68, payload_len 298) and 465 at W=16 (<= 512).
Only Frame B carries Theta on the wire; Frames A/C use the window only for header byte [5].

### C. CCM (D9)
`USER/JX_FLY.sct` is the uVision-generated scatter (banner line edited) plus `RW_IRAM2 0x10000000 0x00010000 { *(MRAC_CCM) }`. `MRAC_CCM` expands to `__attribute__((section("MRAC_CCM"), zero_init))` under `__CC_ARM`, empty on the host. The region is ZI, so `__main` zeroes it through `Region$$Table`; nothing is copied from flash. In the map: `MRAC_CCM` is a `Zero` section at 0x10000000, 0x648 B, and `Region$$Table` grew from 0x20 to 0x30 bytes (one extra entry, the zero-init).
In CCM (1608 B): mrac_state 464 @0x10000000, mrac_config_pitch/roll/yaw/z 172 each @0x100001d0/0x1000027c/0x10000328/0x100003d4, mrac_bus 128 @0x10000480, mrac_g_gamma/sigma/phi 96 each @0x10000500/0x10000560/0x100005c0, mrac_u_ff 16 @0x10000620, static `grad` 24 @0x10000630.
Kept in main SRAM on purpose: `mrac_cyc` (debugger/telemetry-friendly, and 136 B does not matter), `mrac_flags`, `mrac_simplex`, and the sigma-prior `Theta_prior` array.

### D. DWT cycle counters (D11)
`mrac_cyc.last[axis]` / `.max[axis]` = `{bus, blocks, law, total}` per axis, plus `l2_last`/`l2_max` (L2 is called once per tick, not per axis). `MRAC_CYC_NOW()` reads `DWT->CYCCNT` under `__CC_ARM` and is the constant `0U` on the host, so the equivalence dumps and every float value are unaffected. CYCCNT is already enabled at boot by `RPM_DwtInit` (BSP/rpm.c), so no enable code was added. `MRAC_CycEnd()` is called before the freeze early-return and at the end of `MRAC_UpdateAxis`. Deltas are unsigned so a counter wrap is harmless. Writing 0 to `max` from the debugger clears it.
Caveat: `SendProf_Init` (API/send_prof.c) re-zeroes CYCCNT once, which can poison a single max sample right after boot; documented in a code comment.

### E. Descriptor table (D11)
`mrac_feature_desc` (72 B, const, flash) and `const uint8_t mrac_n_features = MRAC_N_FEATURES` (1 B) both link into the ELF by name: 0x08017edc and 0x08017f24. `mrac_n_features` survived armlink section elimination without a `used` attribute.

## Wire identity argument (default variant)

At the default, `MAX_NUM_BASIS = MRAC_CAPACITY = MRAC_N_FEATURES = MRAC_TELEM_WINDOW = 6`. Every expression I replaced in send_data.c (`Buf[5] = MAX_NUM_BASIS` x5, `s_frame_c_buf[5]`, Frame B `total_floats`, the Theta loop bound) therefore evaluates to the same constant as before, so header bytes, payload length, float count and CRC input are unchanged. Empirical check: armcc -O1 object code of HEAD's `send_data.c` (compiled against HEAD's MRAC headers) and of the new file differs only in path strings; the disassembly diff has no instruction differences.

## DMA audit (CCM is not DMA-reachable on STM32F407)

Every DMA memory address in the tree, by grep of `DMA_Memory0BaseAddr` and `->M0AR`:
- UART4 TX (DMA1_Stream7): `Custom_DataBuf`, `Buf_Telemetry_UART4`.
- USART3 TX (DMA1_Stream3): `s_tx_ring[t]`; RX: `UA3RxDMAbuf`.
- USART4 RX: `UA4RxDMAbuf`; USART5 RX: `UA5RxDMAbuf`.
- UART5/subscribe TX (DMA1_Stream7 via `Uart5_Subscribe_TxSend`): callers pass `stream_buf`, `s_transaction_result_buf`, and the `subscribe.c` reply buffer, all static buffers in main SRAM.
- `DMA1_Stream4->M0AR = DataBuf_to_linux`.
None of these is an MRAC object. All MRAC readers (send_data.c, controller.c, main.c, subscribe by address) are CPU loads/stores; the frames are built by CPU copy into the main-SRAM buffers above. The subscribe allowlist already contains `0x10000000..0x1000FFFF` (`SUBSCRIBE_ADDR_CCM_LO/HI`), so subscribed reads of the moved objects still pass validation.

## Gates (measured this session)

1. Equivalence, default:
   `EQUIV OK: 3728208 lines identical (plain) + 4120208 (sigma-prior)`
2. Self-test:
   `SELFTEST OK`
3. Equivalence, capacity 16 (`--define MRAC_CAPACITY=16`):
   `EQUIV OK: 3728208 lines identical (plain) + 4120208 (sigma-prior)`
4. armcc scratch compile (`--cpu Cortex-M4.fp -O1 --c99`): mrac.c plain and sigma-prior and send_data.c compile with 0 errors. Warnings unchanged from HEAD: 4 in mrac.c, 1 in send_data.c. VFMA/VFNMA count in mrac.o (plain and sigma-prior): 0.
5. gcc `-std=c99 -Wall -Wextra -pedantic -fsyntax-only`: 5 warnings in each of default, sigma-prior and `-DMRAC_CAPACITY=16` (same 5 as HEAD).
6. Full Keil build (`UV4 -b -t JX_FLY -j0 JX_FLY.uvprojx`, run from `USER/`): `0 Error(s), 15 Warning(s)`. The HEAD baseline build reported `0 Error(s), 81 Warning(s)` (its first build recompiled every file, mine reused up-to-date objects, so the totals are not comparable); for the two changed files the counts equal HEAD's, mrac.c 4 and send_data.c 1 (gate 4).

## Sizes (Keil, HEAD 6eed617 vs this change)

| | HEAD | S2a | delta |
| --- | --- | --- | --- |
| Code | 97332 | 97556 | +224 |
| RO-data | 4320 | 4344 | +24 |
| RW-data | 2752 | 2752 | 0 |
| ZI-data | 123472 | 123608 | +136 (= `mrac_cyc`) |
| RW_IRAM1 (main SRAM) | 0x1ed10 = 126224 B | 0x1e750 = 124752 B | -1472 B |
| free main SRAM (0x20000 - RW_IRAM1) | 4848 B | 6320 B | +1472 B |
| RW_IRAM2 (CCM) | none | 0x648 = 1608 B of 0x10000 | +1608 B |

Main-SRAM use falls by 1472 B: the CCM objects (1608 B) left it and `mrac_cyc` (136 B) entered it. Total linked RW+ZI is unchanged in meaning (126360 B = 124752 main SRAM + 1608 CCM).
`mrac.o` at armcc -O1: code 3836 B (S1b: 3636 B, +200 B for the cycle stamps).

## Open items

1. CCM readability through the debugger AHB-AP and the subscribe path is unverified (needs a flash, which was forbidden). The CCM is CPU-only on F407: DMA cannot reach it, but SWD reads via the AHB-AP do work on the F4 in general. First thing to check after the next flash: `python -m ground_station.livewatch read mrac_state.roll.What[0]` and `mrac_cyc` by name, then a subscribe of a CCM address.
2. `SendProf_Init` re-zeroes CYCCNT once; one `mrac_cyc.max` sample right after boot may be spurious. Write 0 to `mrac_cyc.max` after the first tick to clear it.
3. Comments and docs still call header byte [5] "MAX_NUM_BASIS" (send_data.c ~97, ~1035; subscribe.h, subscribe.c, usart5.c). The value is now `MRAC_TELEM_WINDOW`; left alone to keep the diff to what the task needs. S2b (ground station) should read the window from header [5] and not assume 6 once the capacity changes.
4. The 4-bit element index in the param command limits capacity to 16 (assert kept). A larger capacity needs a wire change.
5. `Theta_prior[AXES][MAX_NUM_BASIS]` (sigma-prior build only), `mrac_flags`, `mrac_simplex` are still in main SRAM. Moving them later is a one-tag change.
6. `USER/build.txt` (Keil build log) is untracked scratch and not committed.
