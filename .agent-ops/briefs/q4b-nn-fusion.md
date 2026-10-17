# Research: how a learned velocity estimate should enter a drone's EKF (read-only: web + academic)

Context: an indoor quadrotor, STM32F407 (Cortex-M4F, 168 MHz, about 44 KB free RAM). A 2-state-per-axis EKF fuses a
downward optical-flow sensor plus the IMU. Each measurement has a noise R and an innovation gate (outlier reject). A
small GRU (32 units, 20 Hz, 1 s of block-mean features) predicts body-frame xy velocity. Today it is trained offline
on camera ground truth. The model takes the EKF velocity v as an input and outputs a*v + b (a learned calibration).
We want the NN onboard in SHADOW mode (logged, not used) with a switchable ACTIVE mode.

Part 1. State of the art. For each paper, give the year, the venue, how the NN output enters the filter, whether the
NN also predicts its own covariance, and one quote under 15 words with a URL. Verify these and add at most 3 more:
- TLIO (Liu et al., 2020): learned inertial odometry, NN displacement + covariance fused in an EKF.
- Cioffi et al., 2023, "Learned Inertial Odometry for Autonomous Drone Racing".
- AirIO (2025): learned IMU odometry for drones, body-frame velocity.
- AI-IMU dead reckoning (Brossard, Barrau, Bonnabel, 2020): the NN adapts the EKF noise covariances.
- KalmanNet (Revach et al., 2022): the NN computes the Kalman gain.
- Any work that corrects optical-flow scale or nonlinearity with a learned model before the filter.

Part 2. These are the integration options. For each one, what does the literature say about the risks: overconfidence
or double counting when the NN input contains the filter state, an out-of-distribution input such as a new floor
texture, a slow bias inside the gate, and how the deployed systems guard against these (chi-square gate, learned
covariance, fallback)?
 (1) The NN velocity is a separate gated EKF measurement.
 (1b) The NN corrects the raw flow measurement before the EKF update.
 (2) The NN velocity replaces the velocity the controller sees.
 (3) The controller sees the EKF velocity plus a clamped NN residual.
 (4) The NN sets the flow measurement's R.

Part 3 (short). Which on-MCU tools run a GRU on a Cortex-M4F: CMSIS-NN, TensorFlow Lite Micro, STM32Cube.AI
(X-CUBE-AI), or hand-written C? Does each support GRU or recurrent state, float32 versus int8, and is it free? Cite
the docs.

Answer in at most 45 lines. Mark anything you could not source as UNVERIFIED. End with a one-line verdict on which
option the literature supports for a first ACTIVE flight.
