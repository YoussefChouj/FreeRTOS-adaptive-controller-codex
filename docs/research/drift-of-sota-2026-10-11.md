# Optical Flow Drift: Causes and Fixes for Small Hovering Drones

## Bottom Line: Most Likely Causes for OUR System

1. **Unobservable EKF modes in hover**: The system's 8-state EKF currently attempts to estimate both optical flow bias (`bof`) and accelerometer bias (`ba`) while in hover. Without dynamic maneuvers, these states cannot be mathematically separated, causing one or both to wander and produce drift.
   * *Cheapest log test*: Check EKF covariance and state values for `bof` and `ba`; if they drift apart or grow linearly during steady hover, the modes are unobservable.
2. **Gyro-compensation delay**: A small latency (e.g., 10 ms) between the high-frequency IMU and the optical flow sensor causes the gyro compensation to subtract rotation from the wrong time, injecting motion into the velocity estimate.
   * *Cheapest log test*: Plot optical flow innovations against gyro rates (`gyrox`, `gyroy`). Correlated spikes during small attitude adjustments indicate a delay mismatch.
3. **Physical tilt / Scale Error**: Small physical mounting tilts of the sensor relative to the IMU result in a constant flow bias in the body frame. 
   * *Cheapest log test*: Check if the drift direction is fixed in the body frame regardless of heading (e.g., drone always slides to its own "right" in hover).
4. **Height-scale coupling**: The ToF height measurement directly scales the flow output ($v = h \cdot \omega$). ToF noise or uncompensated drone tilt stretches the ground distance, creating velocity noise.
   * *Cheapest log test*: Compare ToF height variance against velocity innovation variance during hover.

---

## 1. Flow-velocity bias sources

Optical flow sensors compute translational velocity by tracking pixel movement and subtracting the drone's rotation (measured by the gyro). Key bias sources include:
* **Physical tilt error**: An uncalibrated angular offset between the IMU and the flow sensor. As noted by a user troubleshooting PX4 drift, "Changing the pitch angle and the yaw angle of the sensor relatively to the Pixhawk ... affected the drift direction and magnitude." [1]
* **Gyro-compensation delay**: A time latency between gyro and flow measurements. At 0.3-1 m heights, uncompensated latency during a rapid rotation can easily inject 1-10 cm/s of phantom velocity (UNVERIFIED).
* **Lens scale and texture**: Inaccurate focal length parameters or poor lighting/texture cause pixel scaling errors. 

## 2. Observability

In hover, a drone experiences no sustained horizontal acceleration. 
* "In control theory, a system is observable if the internal state of the system can be inferred" [2].
* For an EKF using optical flow and IMU, separating a constant accelerometer bias from a constant flow velocity bias requires variations in attitude and acceleration. Without motion excitation, these states are unobservable. 
* **Implication**: Estimating both `bof` and `ba` simultaneously during a steady hover is a mistake that allows the filter states to wander uncontrollably (UNVERIFIED).

## 3. Height-scale coupling

The optical flow module measures angular velocity $\omega$. To get physical velocity $v$, the filter scales it by height: $v = h \cdot \omega$.
* Any error in the height estimate $\Delta h$ scales linearly into velocity bias: $v_{err} = \Delta h \cdot \omega$. At a 1m hover with a 10% ToF noise, $v_{err}$ can reach 1 cm/s (UNVERIFIED).
* If the drone is tilted by angle $\theta$ and the ToF is not tilt-corrected, the physical distance to the ground increases to $h/\cos(\theta)$. For a 10 degree tilt, this adds a ~1.5% scale error to the velocity (UNVERIFIED).

## 4. Practical fixes used by open-source stacks

Major flight stacks implement specific parameters and filtering to combat these issues:
* **PX4**: Uses an innovation gate to reject bad optical flow measurements. Parameter `EKF2_OF_GATE` sets the "Optical flow innovation consistency gate size." [3]
* **ArduPilot**: Explicitly compensates for lens and mounting scale errors using `FLOW_FXSCALER` and `FLOW_FYSCALER`. Their tuning guide states: "If OF.flowX is larger or smaller than OF.bodyX, then it can be adjusted" [4].
* **Crazyflie**: Employs robust Kalman filtering (M-estimation) in `kalman_core` to reject outlier innovations from the flow deck (UNVERIFIED).

## 5. Absolute fixes

To completely bound drift, an absolute position reference is required.
* Downward cameras tracking fiducial markers (ArUco / AprilTags) or external Visual Inertial Odometry (VIO) cameras can provide absolute position updates.
* When integrating VIO like the Intel T265, vibration is a major cause of drift. As the PX4 documentation warns: "If using the T265 try soft-mounting it (this camera is very sensitive to high-frequency vibrations)." [5]

## 6. Newer methods (2022-2026)

Recent literature addresses EKF consistency issues using Lie-group formulations.
* **Invariant EKF**: "The invariant extended Kalman filter (IEKF) is a version of the extended Kalman filter (EKF) for nonlinear systems possessing symmetries" [6].
* While MSCKF and IEKF show better consistency in dynamic flights, standard EKF is still the most realistic for a resource-constrained STM32F4 (UNVERIFIED).

---

## Sources

[1] PX4 Discuss Forum, "UAV drifts away when hovering based on optical flow". Retrieved from: https://discuss.px4.io/t/uav-drifts-away-when-hovering-based-on-optical-flow/28601 
Quote: "Changing the pitch angle and the yaw angle of the sensor relatively to the Pixhawk ... affected the drift direction and magnitude."

[2] Wikipedia, "Observability". Retrieved from: https://en.wikipedia.org/wiki/Observability
Quote: "In control theory, a system is observable if the internal state of the system can be inferred"

[3] PX4 Parameter Reference, `EKF2_OF_GATE`. Retrieved from: https://docs.px4.io/main/en/advanced_config/parameter_reference.html
Quote: "Optical flow innovation consistency gate size."

[4] ArduPilot Documentation, "Optical Flow Sensor Testing and Setup". Retrieved from: https://ardupilot.org/copter/docs/common-optical-flow-sensor-setup.html
Quote: "If OF.flowX is larger or smaller than OF.bodyX, then it can be adjusted"

[5] PX4 Documentation, "Visual Inertial Odometry (VIO)". Retrieved from: https://docs.px4.io/main/en/computer_vision/visual_inertial_odometry.html
Quote: "If using the T265 try soft-mounting it (this camera is very sensitive to high-frequency vibrations)."

[6] Wikipedia, "Invariant extended Kalman filter". Retrieved from: https://en.wikipedia.org/wiki/Invariant_extended_Kalman_filter
Quote: "The invariant extended Kalman filter (IEKF) is a version of the extended Kalman filter (EKF) for nonlinear systems possessing symmetries"
