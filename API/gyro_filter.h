// ------------------------------------------------------------------------------
// Gyro rate-feedback low-pass filter (Phase 1 of SysID plan — see docs/adr/0004)
// ------------------------------------------------------------------------------
// Per-axis 2nd-order Butterworth biquad low-pass on the inner-loop rate feedback
// (pitch/roll/yaw), applied where gyro*PID.FB is set so the rate PID, MRAC, and the
// system-ID telemetry all see the same filtered signal.
//
// DEFAULT: DISABLED (pass-through) — zero change to current flight behaviour until the
// operator enables it from the dashboard (CMD 0x15) after inspecting the gyro spectrum.
// ------------------------------------------------------------------------------

#ifndef GYRO_FILTER_H
#define GYRO_FILTER_H

#include <stdint.h>

typedef enum {
    GYRO_FILT_PITCH = 0,
    GYRO_FILT_ROLL  = 1,
    GYRO_FILT_YAW   = 2,
    GYRO_FILT_AXES  = 3
} GyroFiltAxis_e;

// Initialize all axes: store sample rate, default cutoff, clear states, DISABLED by default.
void GyroFilter_Init(float fs_hz);

// (Re)compute biquad coefficients for one axis at cutoff fc_hz (clamped to (0, fs/2)).
void GyroFilter_SetCutoff(GyroFiltAxis_e axis, float fc_hz);

// Global enable/disable (1 = filter active, 0 = pass-through). State is preserved while off.
void GyroFilter_SetEnabled(uint8_t on);

// Apply the filter to one axis sample (deg/s in -> deg/s out). Pass-through if disabled.
float GyroFilter_Apply(GyroFiltAxis_e axis, float x);

#endif // GYRO_FILTER_H
