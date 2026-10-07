# State estimation: what this drone has, and the state of the art (2026-10-07, inline research)

## 1. Sources on the drone (read from the code today)
| Source | What it gives | Used today |
|---|---|---|
| BMI088 FC IMU (`API/bmi088_driver.c`) | gyro + accel | attitude; `ekf_of.c` predict |
| ANO flow module (`API/Ano_OF.c`, USART2) | 0x51 flow in 3 modes (0 raw, 1 height-fused, 2 inertial-fused), 0x01 its own IMU, 0x04 its own quaternion, 0x34 height | only mode-2 velocity `of2_d*_fix` + height rate `of2_h_f2_v` |
| RPM x4 (`BSP/rpm.c`, EXTI per motor) | rotor speed | `thrust_estimators.c` shadow only (k_T w^2, mass_hat) |
| Motor commands + battery voltage (`USER/ADC.c`) | thrust-model inputs | thrust LUT shadow |
| TF-Mini Plus lidar (`API/tf_mini_plus.c`) | range | parser only, no caller |
| Phone video (`ground_station/analysis/video_truth.py`) | truth xy, offline | analysis only |

Finding: the flow input in use today is the module's own fused velocity (its IMU + flow + height). The FC filter then fuses it
again with the FC IMU, so the errors are correlated and the FC never sees the raw quantities (flow rate, range, gyro) whose
errors it is trying to estimate (scale, gyro bias, lag).

## 2. Techniques, ranked for this drone (gains are PROPOSED, none measured here)
| # | Technique | Uses | Why it helps | Key sources |
|---|---|---|---|---|
| 1 | Raw-flow measurement model: flow = v_xy / range - w_xy, derotated with the gyro on the flow board, delay buffer, quality gate | flow mode 0 + module IMU + range | estimator sees scale, gyro bias and lag explicitly; PX4 EKF2 standard | PX4 EKF2 docs |
| 2 | Rotor-drag velocity aiding: a_xy,meas = -k_d (sum w_i) v_xy + b_a | RPM + FC accel | horizontal velocity from the accelerometer alone, drift-free, works when flow fails (dark/low-texture floor) | Leishman 2014, Abeywardena 2013, Svacha 2019 (motor speeds, online k_d + biases), AR.Drone (Bristeau 2011: vision + drag model in a product) |
| 3 | Thrust model in z: a_z = k_T sum w_i^2 / m | RPM + FC accel | vertical-velocity aid; online mass / external-force estimate (payload, ground effect) | VIMO (Nisar 2019), HDVIO (Cioffi 2023) |
| 4 | RPM notch filters on the IMU (fundamental + harmonics per motor) | RPM + both IMUs | removes motor vibration with less lag than low-pass filters; cleaner accel makes 2-3 work | Betaflight RPM filter |
| 5 | Two-IMU fusion: virtual IMU averaging, cross-check, time-offset estimate | FC IMU + module IMU | lower noise if errors are independent; fault detection | Virtual IMU (arXiv 2506.00371), arXiv 2209.08895 |
| 6 | Learned inertial odometry with motor speeds | IMU + RPM + truth for training | best published IMU+RPM-only results; needs training data (video truth can supply it), laptop first | DIDO (Zhang 2022), Cioffi 2023 learned IO, AirIO 2025 |
| 7 | Offline smoother (RTS / factor graph) on logs | all | best-possible estimate to tune the onboard filter against | standard |

## 3. Proposed architecture (PROPOSED)
One error-state EKF at 200 Hz on the FC. States: p, v, accel bias, gyro bias xy, flow scale, k_d, k_T, terrain height.
Updates: raw flow (derotated), range (module or TF-Mini), drag-aided accel xy, thrust-aided accel z, ZUPT on ground.
Attitude stays in `imu_update.c`.

## 4. Steps, each checked against video truth
1. One roam log (only when the operator asks, capture rules apply): flow mode 0 raw + module IMU + height + RPM + FC IMU at 200 Hz.
2. Offline fits vs truth: k_d, k_T, flow scale, flow lag, module-vs-FC gyro offset.
3. Replay candidate filters offline (`sim/ekf.py` is the golden model) and pick by truth error.
4. Port the winner in shadow mode, review, `tools/check.sh`, flash, validation roam.

## Sources
- Leishman, Macdonald, Beard 2014, Quadrotors & Accelerometers: http://www.et.byu.edu/~beard/papers/preprints/LeishmanMacdonaldBeard__.pdf
- Abeywardena et al., drift-free velocity estimator: https://arxiv.org/pdf/1509.03388
- Svacha et al. 2019, yaw-independent velocity and attitude: https://ieeexplore.ieee.org/document/8620279/
- Bristeau et al. 2011, AR.Drone navigation: https://www.asprom.com/drone/PJB.pdf
- Model-aided optical flow/inertial fusion (J. Navigation): https://www.cambridge.org/core/journals/journal-of-navigation/article/abs/modelaided-optical-flowinertial-sensor-fusion-method-for-a-quadrotor/B347506B078555D2E7086BB104BA037B
- PX4 optical flow + EKF2: https://docs.px4.io/main/en/sensor/optical_flow , https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf
- DIDO: https://arxiv.org/pdf/2203.03149 ; Cioffi 2022 learned IO: https://arxiv.org/abs/2210.15287 ; HDVIO: https://arxiv.org/abs/2306.11429 ; AirIO: https://arxiv.org/html/2501.15659v1
- Betaflight RPM filter: https://oscarliang.com/rpm-filter/
- Virtual IMU: https://arxiv.org/pdf/2506.00371 ; multi-IMU fusion: https://arxiv.org/pdf/2209.08895
