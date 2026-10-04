Status: DONE (all acceptance commands pass; gate fails on size and on two clang-tidy environment artifacts, below)
Commits: 017d94c firmware ST/LFHG + PR fix + host tests; 6f6633b SIL limits, descriptors, matrix; 4b96817 campaigns, doc;
  d68c26d host-driver extern fix (all on wp/33, base wp/31).
Gate: GATE FAIL: size,clang-tidy (scope PASS 25 files, ruff PASS 9 files, pytest PASS "48 passed in 3.86s"). Size 984/200:
  the brief's four parts cannot fit 200. clang-tidy resolves headers through the main checkout's compile_commands.json,
  i.e. the old API/mrac.h (WP-27 note 3): "no member named 'st_eps'" in API/tests/mrac_sizeof.c (gcc -Wall: clean), and
  WP-31's "'io.h' file not found" in sil_server.c.
Verification: python -m pytest -q sim/sil ground_station/analysis/tests -> "334 passed, 5 skipped, 40 warnings in 540.65s";
  python API/tests/run_mrac_equiv.py -> "EQUIV OK: 3728208 lines identical (plain) + 4120208 (sigma-prior)";
  load_campaign: pr_refmodel, st_mrac, lfhg_mrac OK (test_mrac_wp33_campaigns.py). Limits: 1353 runs, 573 s; matrix 756 runs, 456 s.
Worker rounds: CTE, effort xhigh (no manager or workers, per the brief).
Firmware (CMD 0x1D fields 13-18 appended: st_eps, st_phi_max, st_bar, lf_gain, sigma_lf, gam_f; all default OFF):
- ST: gradient x k_st = eps(eps - |e|/2)/(eps - |e|)^2 (Arabi/Yucelen restricted potential, notebook P2 c6), 1 at e = 0, cap
  st_phi_max; st_bar = the sim's log barrier, in the drive so projection still bounds it. Deviation: the sim
  (ctrl_p2_yucelen.py, uncommitted, read in the main checkout) used unnormalised 1/(eps^2 - s^2) and added the barrier unprojected.
- LFHG: lf_gain > 0 turns on the sigma_lf (Theta - Whatf) pull per axis and multiplies gamma; writing it copies Theta to Whatf.
- PR vs sim: kappa (Theta - Whatf)'Phi, update order and filter match. Mismatch fixed: Whatf froze in the deadzone or with
  learning off, leaving a standing kappa offset (wp/31 build: max|Theta - Whatf| 4.1e-4 after learning stops; now < 1e-6, host
  test). Not changed: crm_ell pulls only the rate model, before e (sim: also the angle state, after u). The notebook's L1-like
  PR (P3 c10, predictor v into u and xm) is a different law and is not in firmware.
- ENABLE_LYAPUNOV_BARRIER (mrac.h:79): no code reads it. mrac_var_id widened to uint16 (bits 0x100 ST, 0x200 barrier, 0x400 LFHG).
- RAM, measured with sizeof on the host (API/tests/mrac_sizeof.c): MRAC_AxisConfig_t 216 -> 232 B, +64 B CCM (ST 48, LFHG 16),
  mrac_var_id +4 B. Ops per axis per tick when ON: ST 11 FP (1 div), barrier +11 (2 div), LFHG 36, PR 50; cycles ~35/45/45/60
  PROPOSED. OFF: 3 compares per axis.
SIL limits (docs/analysis/sil-limits-2026-10-04.md; doublet, p/r injected only, 3 seeds, aborts relative to pid):
- Every variant: gain edge G 1 (ST eps 1.0: 0.5), first failure T tilt (13-14.5 deg vs pid 9.0); no crash up to G 128.
- Flight presets, x4 gain margin: PR G 0.25, kappa 0.5, crm 10 (delay margin +10 ms, noise x2 pass); ST G 0.25, eps 2.0,
  phi_max 10 (+10 ms, pass); LFHG lf_gain 0.25 (+0 ms, noise x2 fails on T by majority). LFHG does not move the edge (same
  as V1); it trims u_ad HF 6-23 % at G >= 4. st_bar 0.2 lifts the +0 ms edge to G 8 but fails +5 ms at every G.
- Hard-freeze relay (firmware guard, not the variants): at +5 ms, |e| > e_freeze zeroes u_ad, which ramps back, ~4 Hz; it is
  the only +5 ms failure of V1/V2/LFHG at G 0.25 (0/3 seeds with hard_freeze_on 0). PROPOSED: hold or fade u_ad instead.
Matrix: all axes injected -> every MRAC row "do not fly" (U on y/z, as WP-31). p/r only (axis mask 0x03): pr, st, lfhg,
  pr+st+lf, v1 x.25 "fly with limits" (F RMSE 1.27/1.13/1.20/1.38/1.20 x pid); v2 "do not fly" (new T on D).
Open for the CEO:
- Descriptors carry p/r knobs only (MAX_KNOBS 14). Flying as tested needs g_ctrl_axis_mask = 3 by probe (no 0x1F handler).
- sim/sil only: CMD 0x7E (gamma past the 0x1D bound of 2) exists in sil_server.c, not in firmware.
- Keil build-check please (new fields, uint16 mrac_var_id). Thresholds, presets and the limit-test rules are PROPOSED.
