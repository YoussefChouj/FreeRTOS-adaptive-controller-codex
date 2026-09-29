STATUS: done

Files changed:
- `API/mrac_variant.h`: Added structural constants and MRAC_VARIANT_STRUCT6.
- `API/mrac.h`: Updated to include mrac_variant.h, add asserts, feature block types, and bus array.
- `API/mrac.c`: Replaced MRAC_Init arrays with MRAC_SET/MRAC_BASIS, added block generator logic in MRAC_UpdateAxis, and L2/L3 hooks.

Verification:
- `python3 API/tests/run_mrac_equiv.py`: EQUIV OK: 3728208 lines identical (plain) + 4120208 (sigma-prior). Pass 1/1.
- `python3 API/tests/run_mrac_equiv.py --self-test`: SELFTEST OK. Pass 1/1.
- GCC syntax check in temp dir: code 0, only preexisting unused param/function warnings on MRAC_InverseMixer. Pass 1/1.

Open questions / risks:
- New globals increase RAM usage by ~432 bytes (bus 128B, hooks 3x96B, feedforward 16B, block desc table).
- L2 hooks default to 1.0f and identity updates. L3 returns 0.0f feedforward and is unused in S1.
- The enum for groups and blocks are provisional and must be expanded when RBF or other features are added.

SUBSTITUTIONS: none
- SUPERVISOR (measured, armcc 5.06 -O1 plain, fromelf -z): the ~432 B above was an estimate; measured rw+zi 1212 -> 1668 B = +456 B (adds static grad 24 B); code 2944 -> 3636 B. Supervisor restored the provenance comments the worker deleted and aligned the tables.
