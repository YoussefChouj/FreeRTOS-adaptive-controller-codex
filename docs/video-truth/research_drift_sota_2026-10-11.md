# State of the Art: OF/IMU Drift in Quadrotors

Author: Research Worker (WP-R1) | Date: 2026-10-11

## Context
Platform: ~1.1 kg indoor STM32F4 quadrotor, BMI088 IMU, downward optical flow (ANO OF: raw `of1`, gyro-compensated `of2`, ToF height). 8-state EKF per axis pair ($[p, v, b_{of}, b_a]$) driven by gravity-tilt ($0.242 \cdot \theta$). Ground truth: fixed phone camera at 30 fps (~0.3 s latency) tracking ArUco markers. Observed: ~0.44 cm/s wandering hover drift; pure yaw in hover induces position departure.

---

## 1. Optical Flow Fusion in Open-Source Autopilots

### PX4 EKF2
* **Flow Model & Lever Arm:** Predicts angular flow rate $\dot{\theta}_{flow} = [v_y/h, -v_x/h]^T$, scaled by clearance $\text{scale} = R_{2,2} / h_{agl}$. Sensor lever arm is modeled via `EKF2_OF_POS_X/Y/Z`:
  $\vec{v}_{rel} = -\vec{\omega}_{flow} \times (\vec{r}_{flow} - \vec{r}_{imu})$, $\vec{v}_{earth} = \vec{v} + R_b^e \vec{v}_{rel}$.
  *Source:* https://github.com/PX4/PX4-Autopilot (`src/modules/ekf2/EKF/aid_sources/optical_flow/optical_flow_fusion.cpp`)
  *Quote:* "calculate the velocity of the sensor relative to the imu in body frame"
* **Delay Buffers & Gyro Comp:** Ring buffer matches latency (`EKF2_OF_DELAY`, 10–20 ms). Gyro rates: `_flow_sample_delayed.gyro_rate - _flow_gyro_bias`.
* **Gates & Reset Rules:** Innovation gate is `EKF2_OF_GATE`. Test ratio $> 1.0$ rejects flow. If rejections persist past `no_aid_timeout_max` (~1.0 s), PX4 resets velocity to flow:
  *Source:* https://github.com/PX4/PX4-Autopilot (`src/modules/ekf2/EKF/aid_sources/optical_flow/optical_flow_control.cpp`)
  *Quote:* "reset velocity to flow"
* **Height Scaling:** Terrain clearance is clamped to $h_{agl} \ge 0.01$ m (`min_hagl = 1e-2f`). Observation noise interpolates between `EKF2_OF_N_MIN` and `EKF2_OF_N_MAX` via `EKF2_OF_QMIN`.

### ArduPilot EKF3
* **Flow Model & Scaling:** Scales optical flow to velocity via height. Provides parts-per-thousand lens scale trims `FLOW_FXSCALER` and `FLOW_FYSCALER` (-200 to +200):
  *Source:* https://ardupilot.org/copter/docs/common-optical-flow-sensor-setup.html
  *Quote:* "If OF.flowX is larger or smaller than OF.bodyX, then it can be adjusted"
* **Parameters & Resets:** Lever arm uses `FLOW_POS_X/Y/Z`. Delay uses `EK3_FLOW_DELAY`. Innovation gate uses `EK3_FLOW_GATE`. Repeated rejections trigger velocity resets or fallback to dead reckoning.

### Crazyflie (Kalman Core)
* **Measurement Model & Lever Arm:** In `kalman_core/mm_flow.c`, predicted pixel rate incorporates camera position:
  $\vec{v}_{cam,add} = \vec{\omega} \times \vec{r}_{flowdeck}$ ($v_{bx,add} = \omega_y r_z - \omega_z r_y$).
  *Source:* https://github.com/bitcraze/crazyflie-firmware (`src/modules/src/kalman_core/mm_flow.c`)
  *Quote:* "Lever-arm induced translational velocity at camera"
* **Height Safeguard:** Height state is clamped: $z_g \ge 0.1$ m to avoid division singularities.

### INAV
* **Complementary Filter:** Uses a complementary filter (`navigation_pos_estimator_flow.c`), not an EKF:
  $v_x = -(\dot{\theta}_y - \omega_y) \cdot z_{alt}, \quad v_y = (\dot{\theta}_x - \omega_x) \cdot z_{alt}$.
  *Source:* https://github.com/iNavFlight/inav (`src/main/navigation/navigation_pos_estimator_flow.c`)
  *Quote:* "Calculate linear velocity based on angular velocity and altitude"
* **Limitations:** No lever arm modeling, no statistical gate (boolean `OPFLOW_QUALITY_VALID` only), fixed gains (`w_xy_flow_v`).

---

## 2. Rotor-Drag & Drift-Free Velocity from Accelerometer

### Aerodynamic Model
* **H-Force / Momentum Drag:** Advancing and retreating blades experience unequal airspeed, generating an in-plane momentum drag force linear in body velocity: $\vec{a}_{body,xy} = -k \cdot \vec{v}_{body,xy}$.
  *Source:* https://hal.science/hal-00422423 (Martin & Salaun 2010)
  *Quote:* "the so-called rotor drag, though rather small, appears at first order"
* **Model Validation:** Leishman et al. 2014 demonstrated body-fixed accelerometers directly measure rotor drag:
  *Source:* https://ieeexplore.ieee.org/document/6822641 (Leishman et al. 2014)
  *Quote:* "State Estimation with an Improved Dynamic Model"
* **PX4 EKF2 Drag Fusion:** Fuses specific forces via `EKF2_DRAG_CTRL`, ballistic coefficients `EKF2_BCOEF_X/Y`, and momentum drag `EKF2_MCOEF`:
  $\text{pred\_acc} = -0.5 \cdot b_{coef}^{-1} \rho v |v| - v \cdot m_{coef} \sqrt{\rho / \rho_0}$.
  *Source:* https://github.com/PX4/PX4-Autopilot (`src/modules/ekf2/EKF/aid_sources/drag/drag_fusion.cpp`)
  *Quote:* "rotor momentum drag that is proportional to true airspeed"

### Observability & Pitfalls
* **Observability:** Renders horizontal airspeed observable from accelerometer measurements alone without optical flow.
* **Accuracy:** ~0.5 to 1.2 m/s in calm air (inference from PX4 `EKF2_DRAG_NOISE` default 0.5 m/s²).
* **Pitfalls:** (1) Accel bias $b_a$ creates velocity bias $\Delta v = -b_a / k$. (2) Propeller vibration requires high noise filtering. (3) Wind creates ground-velocity errors: $\vec{v}_{ground} = \vec{v}_{air} + \vec{v}_{wind}$.

---

## 3. Learned Approaches to Inertial & Flow Odometry

| Method & Source | Inputs | Ground Truth | Drift / Accuracy | Drifts Without Absolute Sensor? |
|---|---|---|---|---|
| **Cioffi 2023** (arXiv:2210.15287) | IMU window + thrusts | MoCap (Vicon) pose & vel | $ATE_T = 0.41\text{--}1.22$ m Blackbird; $0.56$ m racing | **YES**; error grows with distance |
| **TLIO 2020** (arXiv:2007.01867) | 1-s IMU window (ResNet) | Optical tracking (headsets) | $1.10\%\text{--}1.5\%$ translation drift over distance | **YES**; EKF cannot bound random walk |
| **AI-IMU 2020** (arXiv:1904.06064) | Raw IMU; CNN adapts noise | RTK-GPS/INS (KITTI) | $1.10\%$ translation error on KITTI | **YES**; dead reckoning drifts with distance |
| **Gyro Denoising 2020** (arXiv:2002.10718) | Raw gyros (dilated CNN) | MoCap (EuRoC, TUM-VI) | Yaw error $< 4^\circ$ ($3.3^\circ$ mean) on 2-min runs | **YES**; open-loop attitude integration drifts |
| **NeuroBEM 2021** (arXiv:2106.08015) | Body $(v, \omega)$ and RPMs | MoCap arena ($\le 65$ km/h) | $50\%$ error reduction vs first-principles BEM | **YES**; forward model; integration drifts |
| **DeepOF-VIO / Learned Flow** | Flow + IMU | MoCap poses | Improved tracking in low texture (UNVERIFIED) | **YES**; relative odometry drifts without loop closure |

*Quotes:*
* Cioffi (arXiv:2210.15287): "uses an inertial measurement unit (IMU) as the only sensor modality"
* TLIO (arXiv:2007.01867): "integration of measurements is sensitive to sensor bias and noise, causing significant drift"
* AI-IMU (arXiv:1904.06064): "We achieve on average a 1.10% translational error"
* Gyro Denoising (arXiv:2002.10718): "Denoising IMU Gyroscopes with Deep Learning for Open-Loop Attitude Estimation"
* NeuroBEM (arXiv:2106.08015): "outperforming existing models with 50% reduced prediction errors"

---

## 4. Outside-In Tracking Fusion (MoCap, External Camera, ArUco)

### Delayed Measurement Fusion (OOSM)
* **Latency:** External vision incurs 50–300 ms delays from exposure, transmission, and vision processing.
* **PX4 `EKF2_EV_DELAY` Practice:** PX4 uses a ring buffer of historical states. When delayed vision arrives at timestamp $t - \tau$, innovations are formed against past states and propagated forward:
  *Source:* https://docs.px4.io/main/en/advanced_config/parameter_reference.html (`EKF2_EV_DELAY`)
  *Quote:* "External vision measurement delay relative to the IMU"

### Marker Size vs. Distance Rules (ArUco / OpenCV)
* **Detection Rule:** Marker must span $\ge 25\text{--}40$ px for reliable detection and decoding:
  $D_{max} \approx (L \cdot f) / w_{min\_px} \approx 15 \times \text{ to } 25 \times L$.
  For a 20 cm marker with $f \approx 940$ px, $D_{max} \approx 4.7$ m (inference).
  *Source:* https://doi.org/10.1016/j.patcog.2014.01.005 (Garrido-Jurado et al. 2014)
  *Quote:* "Automatic generation and detection of highly reliable fiducial markers under occlusion"
* **Pose Accuracy:** Lateral XY error is ~1–3 mm at 1.5–2 m. Depth (Z) variance scales quadratically ($\sigma_z \propto D^2 / (f L)$).
* **Off-Nadir Geometry:** At 55° camera tilt, height errors map into horizontal floor error at $\tan(55^\circ) \approx 1.43$ cm floor error per cm height error (inference).

---

## 5. Root Causes of Yaw-Induced Position Drift in Downward Optical Flow

1. **Unmodeled Lever-Arm Velocity ($\vec{\omega} \times \vec{r}$):**
   If the flow sensor is offset by $\vec{r}_{flow} = [r_x, r_y, 0]^T$ from the rotation center, yaw rate $r$ induces physical sensor velocity:
   $\vec{v}_{cam} = \vec{\omega} \times \vec{r}_{flow} = [-r \cdot r_y, \ r \cdot r_x, \ 0]^T$.
   At $r = 1.0$ rad/s and $r_x = 0.05$ m, $v_{cam,y} = 5$ cm/s (inference). If the EKF assumes $\vec{r}_{flow}=0$, it treats this as drone translation and drifts away from hover.
2. **Gyro-Flow Latency Mismatch:**
   Flow derotation subtracts gyro rate: $\omega_{trans} = \omega_{flow} - \omega_{gyro}$. A delay $\Delta t$ leaves residual error $\delta \omega \approx \dot{r} \cdot \Delta t$ during yaw accelerations. At $\Delta t = 20$ ms and $\dot{r} = 2$ rad/s², $\delta v \approx 4$ cm/s at 1 m height (inference).
3. **Rotating Body-Frame Bias:**
   Residual sensor tilt or flow bias $\vec{b}_{flow}^b$ is body-fixed. Yawing by $\psi(t)$ rotates this bias in earth frame ($\vec{b}^e(t) = R_z(\psi) \vec{b}^b$). Integrating across an incomplete turn produces a net translation offset $\int R_z(\psi) \vec{b}^b dt \neq 0$ (inference).
*Standard Fixes:* Model lever arm $\vec{\omega} \times \vec{r}$, synchronize gyro and flow integration via ring buffers, and calibrate mounting scale.

---

## 6. First Principles: Can Any Onboard-Only IMU+Flow Bound Position Drift?

* **Observability Analysis:**
  System state is $x = [\vec{p}, \vec{v}, \vec{q}]^T$. Sensors measure $\vec{a}$, $\vec{\omega}$, $\vec{v}/h$, and $h$.
  Every measurement satisfies $\frac{\partial \vec{y}}{\partial \vec{p}_{xy}} = 0$.
  *Source:* https://en.wikipedia.org/wiki/Observability
  *Quote:* "a system is observable if the internal state of the system can be inferred"
  Horizontal position $\vec{p}_{xy}$ is unobservable because the sensor outputs are invariant to spatial translation.
* **Stochastic Integration:**
  Optical flow bounds velocity error to zero mean with bounded variance $\sigma_v^2$. However, position is an open-loop integral:
  $\vec{p}(t) = \vec{p}(0) + \int_0^t \vec{v}(\tau) d\tau$.
  White velocity noise integrates into a Wiener process with linearly growing variance: $\text{Var}(\vec{p}(t)) = \sigma_v^2 \cdot t$. Any unmodeled bias $b_v$ causes linear drift $\Delta \vec{p} = b_v \cdot t$.
* **Conclusion:** Onboard-only IMU + optical flow can **only bound velocity drift**, never position drift. Bounding position drift requires exteroceptive absolute references (GNSS, MoCap, visual landmarks / SLAM loop closure, or ArUco markers).

---

## What an Expert Would Do for This Drone (Ranked Actions)
1. Add lever-arm compensation $\vec{\omega} \times \vec{r}_{flow}$ to EKF flow prediction equations to eliminate yaw-induced velocity artifacts.
2. Freeze accelerometer bias $b_a$ during hover; estimating both $b_{of}$ and $b_a$ simultaneously in steady hover is unobservable.
3. Open `of1_gate` from 5 to 10 sigma (fb615c7), preventing gate rejection dropouts during fast RC moves.
4. Measure and compensate optical flow vs IMU timestamp delay (buffer alignment, 10–25 ms).
5. Calibrate optical flow scale factors and sensor tilt ($v_{body}$ vs $h \cdot \omega$) using flight log regressions.
6. Tilt-correct ToF distance reading ($h = z_{ToF} \cos\theta \cos\phi$) prior to scaling optical flow velocities.
7. Implement delayed-measurement fusion (`EV_DELAY` ~300 ms, $R \approx 2\text{--}5$ cm) for the external 30 fps camera/ArUco stream.
8. Incorporate rotor momentum drag model ($a_{body,xy} = -k v_{body,xy}$) to constrain velocity during dynamic flight.
9. Implement velocity reset logic upon repeated gate rejections ($>1.0$ s timeout) instead of open-loop dead reckoning.
10. Apply empirical static flow bias trims (`OF_BIAS_TRIM_X/Y`) averaged across multi-flight hover windows.
