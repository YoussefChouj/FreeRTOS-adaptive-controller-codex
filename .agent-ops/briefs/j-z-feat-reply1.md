Approach approved; proceed and open the PR without further questions unless blocked. Answers:

1. h: use `Ctrler.Z_posPID.FB` (metres, the value the Z loop and API/sysid.c already use).
2. v_z: use `Ctrler.Z_ratePID.FB`. Check its unit in API/controller.c; if it is not m/s, convert so vz_ref is in m/s,
   and state the unit in the code comment and PR body.
3. Tilt: cos(tilt) = cos(roll) * cos(pitch) from `imu_data.rol` / `imu_data.pit` (degrees -> rad with MRAC_DEG2RAD,
   as API/mrac.c:667 does). Use your own `tilt_max` in the new `MRAC_Z_FEAT_ROW` table; do not reuse prearm's limit.
4. Mask 0 must stay bit-exact for every basis, including the `phi[6] = MRAC_SagPhi(un)` S10X path. New features
   fill only free Z slots; list the slot indices and which basis they apply to in the PR body.
5. All constants PROPOSED; NaN / out-of-range input -> feature 0. Host C tests as in the brief; all
   `python tools/host_tests.py` pass.
