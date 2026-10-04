/* Flight telemetry groups (WP-37): the key flight state as one contiguous block of float32, for the subscribe
 * stream (API/subscribe.h, CMD 0x21).
 *
 * WHY. A subscribe slot carries up to 62 ranges; each range is one contiguous run of equal-size values. The
 * live variables (Ctrler.*PID.Des/FB/U, mrac_state.<axis>.*, imu_data, ...) are spread over many structs with
 * other fields in between, so a slot spends one range per variable. g_tlm copies them, once per control tick,
 * into fixed groups of floats: one range streams a group, and one range (size 4, count FLIGHT_TLM_FLOATS)
 * streams all of them. The copy is taken at one point of the tick (the end of stabilizer_Task, after the motor
 * output), so the values in a group belong to the same tick, which separately read variables do not
 * guarantee (Send_Task can be preempted by Stabilizer_Task between two of them).
 *
 * Nothing is renamed: every existing symbol the ground station reads stays where it is. The member list is
 * mirrored by ground_station/platform/telemetry_groups.py; its test parses this file, so a field added here
 * must be added there in the same commit. All fields are float32 (the ground station coalesces a run of
 * same-size, same-format values into one range).
 *
 * Writer: Tlm_Snapshot() in TASK/StabilizerTask.c, 200 Hz. Readers: the subscribe path (Send_Task) and SWD.
 * Placement: CCM (CPU-only memory; no DMA reads it), 0 B of SRAM.
 */
#ifndef FLIGHT_TELEMETRY_H
#define FLIGHT_TELEMETRY_H

typedef struct {            /* attitude and body rates */
    float roll_deg;         /* imu_data.rol as sampled this tick (= Ctrler.rollPID.FB) */
    float pitch_deg;        /* imu_data.pit as sampled this tick (= -Ctrler.pitchPID.FB) */
    float yaw_deg;          /* imu_data.yaw as sampled this tick (= -Ctrler.yawPID.FB) */
    float rate_x_dps;       /* Ctrler.gyroxPID.FB (roll rate, filtered) */
    float rate_y_dps;       /* Ctrler.gyroyPID.FB (pitch rate, controller sign) */
    float rate_z_dps;       /* Ctrler.gyrozPID.FB (yaw rate, controller sign) */
} TlmAttitude_t;

typedef struct {            /* setpoints of every loop, controller frame */
    float roll_deg;         /* Ctrler.rollPID.Des */
    float pitch_deg;        /* Ctrler.pitchPID.Des */
    float yaw_deg;          /* Ctrler.yawPID.Des */
    float rate_x_dps;       /* Ctrler.gyroxPID.Des */
    float rate_y_dps;       /* Ctrler.gyroyPID.Des */
    float rate_z_dps;       /* Ctrler.gyrozPID.Des */
    float x_cm;             /* Ctrler.locxPID.Des */
    float y_cm;             /* Ctrler.locyPID.Des */
    float vx_cms;           /* Ctrler.locxsPID.Des */
    float vy_cms;           /* Ctrler.locysPID.Des */
    float z_m;              /* Ctrler.Z_posPID.Des */
    float vz_mps;           /* Ctrler.Z_ratePID.Des */
} TlmSetpoint_t;

typedef struct {            /* position and velocity feedback */
    float x_cm;             /* Ctrler.locxPID.FB (OF world frame) */
    float y_cm;             /* Ctrler.locyPID.FB */
    float vx_cms;           /* Ctrler.locxsPID.FB */
    float vy_cms;           /* Ctrler.locysPID.FB */
    float z_m;              /* Ctrler.Z_posPID.FB (ToF) */
    float vz_mps;           /* Ctrler.Z_ratePID.FB */
} TlmPosition_t;

typedef struct {            /* controller outputs and motor commands, CCR (2000 stop .. 4000 full) */
    float throttle;         /* Throttle_out (after the throttle window) */
    float u_roll;           /* u_gyrox */
    float u_pitch;          /* u_gyroy (mixer sign) */
    float u_yaw;            /* u_gyroz */
    float m1;               /* mymotor.motor1 at the end of the tick (Set_PWM_Motors clamps it in place) */
    float m2;               /* mymotor.motor2 */
    float m3;               /* mymotor.motor3 */
    float m4;               /* mymotor.motor4 */
} TlmMotor_t;

typedef struct {            /* MRAC per axis, order pitch, roll, yaw, z (mrac_state.<axis>) */
    float e[4];             /* tracking error x - xm */
    float u_nom[4];         /* PID output in MRAC units */
    float u_ad[4];          /* adaptive correction */
    float fade;             /* mrac_simplex.fade, u_ad multiplier */
} TlmMrac_t;

typedef struct {            /* optical-flow estimators */
    float kf_x_m;           /* s_ekf_of.x[0] (body frame, m) */
    float kf_y_m;           /* s_ekf_of.x[3] */
    float kf_vx_mps;        /* s_ekf_of.x[1] */
    float kf_vy_mps;        /* s_ekf_of.x[4] */
    float kf_bof_x_mps;     /* s_ekf_of.x[2] (OF bias) */
    float kf_bof_y_mps;     /* s_ekf_of.x[5] */
    float of_bias_x;        /* s_of_bias_x (raw OF counts) */
    float of_bias_y;        /* s_of_bias_y */
} TlmEstimator_t;

typedef struct {            /* battery and thrust */
    float vbat_v;           /* real_voltage (1 Hz) */
    float thrust_imu_n;     /* g_thrust_est.imu_total */
    float thrust_be_n[4];   /* g_thrust_est.blade_element[0..3] */
    float mass_hat_kg;      /* g_thrust_est.mass_hat */
} TlmPower_t;

typedef struct {
    TlmAttitude_t  att;
    TlmSetpoint_t  sp;
    TlmPosition_t  pos;
    TlmMotor_t     mot;
    TlmMrac_t      mrac;
    TlmEstimator_t est;
    TlmPower_t     pwr;
} FlightTelemetry_t;

#define FLIGHT_TLM_FLOATS ((unsigned)(sizeof(FlightTelemetry_t) / sizeof(float)))   /* 60 */

extern FlightTelemetry_t g_tlm;

#endif /* FLIGHT_TELEMETRY_H */
