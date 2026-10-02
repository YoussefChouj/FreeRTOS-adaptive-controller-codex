# Status
STATUS: done

# Files Changed
- `API/ekf_of.c`: Implemented 8-state EKF model with accelerometer input and ZUPT.
- `API/ekf_of.h`: Updated struct and function signatures for the 8-state model.
- `TASK/StabilizerTask.c`: Updated Mode 2 block to integrate accel & ZUPT, use 8-state EKF, updated comments.
- `ground_station/comm/boot_default_layout.py`: Added `s_ekf_of.x[6]` and `s_ekf_of.x[7]` to `DASHBOARD_FLIGHT_POSITION_VARS`.
- `ground_station/analysis/ekf_of_model.py`: Created Python twin of 8-state EKF (and `OldEkfOf6` baseline).
- `ground_station/analysis/ekf_of_replay.py`: Created replay tuning CLI script over grid search.
- `ground_station/analysis/tests/test_ekf_of_model.py`: Wrote unit tests checking C logic vs Python.

# Verification
- `gcc -std=c99 -Wall -Wextra -Werror -c API/ekf_of.c -o /tmp/ekf_of.o`: Passed.
- `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_ekf_of_model.py ground_station/comm -q`: Passed (all tests passed).
- `PYTHONPATH=. python -m ground_station.analysis.ekf_of_replay --logs-dir /nonexistent`: Output "Log dir not found: /nonexistent", Exit 2.

# OF Frame Rate & Layout Finding
- UART config for Ano_OF sets BaudRate to 500000 (`BSP/usart2.c:20`). There is no explicit frame rate declared in the `Ano_OF.c` or its header; it operates asynchronously on received bytes.
- `of_update_cnt` does NOT appear in any log layout (grep checked all python and yaml configs).

# Open Risks
- Real-world integration of `ofx3` at interpolated slot1 timestamps might be jittery; not verified on physical hardware logs since they are untracked.
- Hardware testing is blocked (no probe, no drone access in this environment).
- ZUPT threshold uses `flight_phase in {0,3}`, which assumes precise phase transitions.

SUBSTITUTIONS: none
