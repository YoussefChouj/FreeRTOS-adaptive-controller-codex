// ------------------------------------------------------------------------------
// Automated system-identification excitation module — implementation (ADR-0004)
// ------------------------------------------------------------------------------
#include "sysid.h"
#include <math.h>
#include "global_declare.h"   // DroneStatus, FlyMode_SDK
#include "pid.h"              // Ctrler (CtrlerTypeDef global)
#include "mrac.h"             // mrac_flags.id_frame_on
#include "rc_input.h"         // RCInput_IsActive (RC dead-man)

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

// ---- Tunable safety/shape constants -------------------------------------------------
#define SYSID_RAMP_T        1.5f     // [s] cosine ramp in/out
#define SYSID_RECOVERY_T    2.0f     // [s] settle time in RECOVERY before returning to IDLE
#define SYSID_SOFT_XY_CM    50.0f    // [cm] green-zone soft boundary (abort). Closed-loop dither
                                     // (StabilizerTask: += injection) holds station, so this is a
                                     // pure safety net now, not the run-limiting factor — kept tight
                                     // for maximum wall margin.
#define SYSID_ALT_MIN_M     0.30f    // [m] above ground effect
#define SYSID_ALT_MAX_M     1.50f    // [m] below ceiling margin
#define SYSID_ANGLE_LIM_DEG 30.0f    // [deg] attitude abort on excited/any axis
#define SYSID_MS_K          20       // multisine component count (denser FRF -> resolves 2nd-order+
                                     // dynamics; log-spaced over [f0,f1], Schroeder-phased low crest)
#define SYSID_MS_PREEMP     1.0f     // multisine spectral pre-emphasis exponent (fixes the "energy
                                     // split" problem). Per-tone amplitude weight = (f_k/f0)^PREEMP.
                                     // The roll/pitch rate plant is integrator-like (|G| ~ 1/w) over
                                     // the low band, so a flat-amplitude multisine puts almost no
                                     // OUTPUT energy at high f -> coherence dies above ~5 Hz. PREEMP=1
                                     // makes the input rise ~linearly with f, cancelling the 1/w roll-
                                     // off so the measured output (hence coherence/SNR) is ~flat across
                                     // [f0,f1]. 0 = flat input (old behaviour). Peak is re-normalized
                                     // after weighting, so the safety amplitude clamp is unchanged.

// Per-axis amplitude clamps (rate units: deg/s for P/R/Y, m/s for Z)
static float sysid_amp_max(SysID_Axis_e a)
{
    switch (a) {
        case SYSID_AXIS_PITCH:
        case SYSID_AXIS_ROLL:  return 90.0f;  // deg/s
        case SYSID_AXIS_YAW:   return 60.0f;  // deg/s
        case SYSID_AXIS_Z:     return 0.40f;  // m/s
        default:               return 0.0f;
    }
}

// ---- Module state -------------------------------------------------------------------
static SysID_State_e s_state = SYSID_IDLE;
static SysID_Axis_e  s_axis;
static SysID_Signal_e s_sig;
static float s_f0, s_f1, s_amp, s_duration;
static float s_t_sig;        // [s] time since RAMP_IN start
static float s_t_recover;    // [s] time in RECOVERY
static float s_phase;        // chirp phase accumulator
static float s_rate_cmd;     // current excitation rate setpoint (axis native unit)
static float s_cx, s_cy, s_cz; // captured green-zone centre (cx,cy in cm; cz in m)
static float s_ms_f[SYSID_MS_K];
static float s_ms_phi[SYSID_MS_K];
static float s_ms_amp[SYSID_MS_K]; // per-tone amplitude weight (frequency pre-emphasis, see SYSID_MS_PREEMP)
static float s_ms_norm = 1.0f;  // peak-normalization so multisine peak ~= amp (safety)
static uint8_t s_geofence_en = 1U; // green-zone XY geofence enable (CMD 0x14 idx 7; default ON)

// ---- Helpers ------------------------------------------------------------------------
static float attitude_deg(SysID_Axis_e a)
{
    switch (a) {
        case SYSID_AXIS_PITCH: return Ctrler.pitchPID.FB;
        case SYSID_AXIS_ROLL:  return Ctrler.rollPID.FB;
        default:               return 0.0f; // yaw/Z: not angle-limited the same way
    }
}

static void sysid_finish(void)
{
    s_state = SYSID_IDLE;
    s_rate_cmd = 0.0f;
    mrac_flags.id_frame_on = 0; // restore normal A/B telemetry
}

static void sysid_enter_recovery(void)
{
    s_state = SYSID_RECOVERY;
    s_t_recover = 0.0f;
    s_rate_cmd = 0.0f;
    // Release override (IsAxisActive() will now be false) and command return-to-centre.
    // The existing position cascade pulls the drone back; the excited axis re-levels via its
    // outer angle loop now that we no longer override its rate setpoint.
    Ctrler.locxPID.Des   = s_cx;
    Ctrler.locyPID.Des   = s_cy;
    Ctrler.Z_posPID.Des  = s_cz;
}

// Returns 1 if any abort condition currently holds.
static int sysid_abort_condition(void)
{
    if (DroneStatus.ARM_Status == 0) return 1;
    if (DroneStatus.FlyMode != FlyMode_SDK) return 1;
    // RC dead-man: any attitude stick deflected -> instant pilot takeover.
    if (RCInput_IsActive(RC_AXIS_PITCH) || RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_YAW)) return 1;
    // Altitude band.
    if (Ctrler.Z_posPID.FB < SYSID_ALT_MIN_M || Ctrler.Z_posPID.FB > SYSID_ALT_MAX_M) return 1;
    // Green-zone soft boundary (cm). Skipped when the geofence is disabled (pilot-watch override).
    if (s_geofence_en) {
        if (fabsf(Ctrler.locxPID.FB - s_cx) > SYSID_SOFT_XY_CM) return 1;
        if (fabsf(Ctrler.locyPID.FB - s_cy) > SYSID_SOFT_XY_CM) return 1;
    }
    // Attitude runaway (any axis).
    if (fabsf(Ctrler.pitchPID.FB) > SYSID_ANGLE_LIM_DEG) return 1;
    if (fabsf(Ctrler.rollPID.FB)  > SYSID_ANGLE_LIM_DEG) return 1;
    return 0;
}

// Raw signal in ~[-1,1] at the current s_t_sig (advances s_phase for the chirp).
static float sysid_signal_raw(void)
{
    if (s_sig == SYSID_SIG_MULTISINE) {
        float acc = 0.0f;
        int k;
        for (k = 0; k < SYSID_MS_K; k++) {
            acc += s_ms_amp[k] * sinf(2.0f * M_PI * s_ms_f[k] * s_t_sig + s_ms_phi[k]);
        }
        return acc / s_ms_norm; // peak-normalized at Start so |out| <= ~1 (peak ~= amp)
    } else {
        // Log chirp: hold f0 during ramp-in, sweep f0->f1 across the RUNNING window, hold f1 in ramp-out.
        float tau = (s_t_sig - SYSID_RAMP_T) / (s_duration > 1e-3f ? s_duration : 1e-3f);
        float f;
        if (tau < 0.0f) tau = 0.0f;
        if (tau > 1.0f) tau = 1.0f;
        f = s_f0 * powf(s_f1 / s_f0, tau);
        s_phase += 2.0f * M_PI * f * SYSID_DT;
        return sinf(s_phase);
    }
}

// Amplitude envelope (cosine ramp in/out) as a function of s_t_sig; also drives state transitions.
static float sysid_envelope(void)
{
    float t_run_end  = SYSID_RAMP_T + s_duration;
    float t_full_end = t_run_end + SYSID_RAMP_T;
    if (s_t_sig < SYSID_RAMP_T) {
        s_state = SYSID_RAMP_IN;
        return 0.5f * (1.0f - cosf(M_PI * s_t_sig / SYSID_RAMP_T));
    } else if (s_t_sig < t_run_end) {
        s_state = SYSID_RUNNING;
        return 1.0f;
    } else if (s_t_sig < t_full_end) {
        float te = (s_t_sig - t_run_end) / SYSID_RAMP_T;
        s_state = SYSID_RAMP_OUT;
        return 0.5f * (1.0f + cosf(M_PI * te));
    }
    return -1.0f; // sentinel: excitation window complete
}

// ---- Public API ---------------------------------------------------------------------
uint8_t SysID_Start(SysID_Axis_e axis, SysID_Signal_e sig, float f0, float f1, float amp, float duration)
{
    float amax;
    int k;

    if (s_state != SYSID_IDLE) return 0;          // a run is already active
    if (axis < SYSID_AXIS_PITCH || axis > SYSID_AXIS_Z) return 0;
    // Z-axis excitation is not yet wired: Compute_Motor only overrides the P/R/Y rate
    // setpoints (StabilizerTask.c), there is no Z_ratePID.Des injection, and Z lacks its
    // own altitude/ground-effect abort guards. Reject Z so a run can't report "active"
    // while doing nothing. Full Z wiring is follow-up (ADR-0004 finding #1).
    if (axis == SYSID_AXIS_Z) return 0;

    // Preconditions (firmware-checkable gates).
    if (DroneStatus.ARM_Status == 0) return 0;
    if (DroneStatus.FlyMode != FlyMode_SDK) return 0;
    if (Ctrler.Z_posPID.FB < SYSID_ALT_MIN_M || Ctrler.Z_posPID.FB > SYSID_ALT_MAX_M) return 0;
    // Must start inside the green zone (skipped when the geofence is disabled).
    if (s_geofence_en &&
        (fabsf(Ctrler.locxPID.FB) > SYSID_SOFT_XY_CM || fabsf(Ctrler.locyPID.FB) > SYSID_SOFT_XY_CM)) return 0;

    // Sanitize parameters.
    if (f0 < 0.1f) f0 = 0.1f;
    if (f1 < f0)   f1 = f0;
    amax = sysid_amp_max(axis);
    if (amp < 0.0f) amp = -amp;
    if (amp > amax) amp = amax;
    if (duration < 1.0f)  duration = 1.0f;
    if (duration > 60.0f) duration = 60.0f;

    s_axis = axis; s_sig = sig;
    s_f0 = f0; s_f1 = f1; s_amp = amp; s_duration = duration;
    s_t_sig = 0.0f; s_phase = 0.0f; s_rate_cmd = 0.0f;

    // Capture green-zone centre (cx,cy in cm; cz in m). OF origin is reset by the CMD handler first.
    s_cx = Ctrler.locxPID.FB;
    s_cy = Ctrler.locyPID.FB;
    s_cz = Ctrler.Z_posPID.FB;

    // Precompute multisine frequencies (log-spaced), Schroeder phases, and pre-emphasis weights.
    // Weight (f_k/f0)^PREEMP boosts high-f tones to counter the plant's low-pass roll-off so the
    // OUTPUT energy (and coherence) is balanced across the band instead of starving above ~5 Hz.
    for (k = 0; k < SYSID_MS_K; k++) {
        float frac = (SYSID_MS_K > 1) ? ((float)k / (float)(SYSID_MS_K - 1)) : 0.0f;
        s_ms_f[k]   = f0 * powf(f1 / f0, frac);
        s_ms_phi[k] = -M_PI * (float)k * (float)k / (float)SYSID_MS_K;
        s_ms_amp[k] = powf(s_ms_f[k] / f0, SYSID_MS_PREEMP);
    }
    // Peak-normalize: weighted log-spaced tones leave a crest factor ~4, so RMS-scaling would
    // overshoot the commanded amplitude 2-3x. Scan the WEIGHTED waveform once and divide by its
    // peak so |out| <= ~1 (peak ~= amp) -> the safety amplitude clamp holds regardless of PREEMP.
    {
        float tscan = (duration < 8.0f) ? duration : 8.0f;
        float tt, peak = 1e-6f;
        int n;
        for (tt = 0.0f; tt <= tscan; tt += 0.002f) {
            float acc = 0.0f;
            for (n = 0; n < SYSID_MS_K; n++)
                acc += s_ms_amp[n] * sinf(2.0f * M_PI * s_ms_f[n] * tt + s_ms_phi[n]);
            if (fabsf(acc) > peak) peak = fabsf(acc);
        }
        s_ms_norm = peak * 1.05f; // 5% margin
    }

    mrac_flags.id_frame_on = 1; // auto-enable the high-rate ID capture
    s_state = SYSID_RAMP_IN;
    return 1;
}

void SysID_Abort(void)
{
    if (s_state == SYSID_IDLE) return;
    sysid_enter_recovery();
}

void SysID_Update(void)
{
    float env, raw;

    if (s_state == SYSID_IDLE) {
        s_rate_cmd = 0.0f;
        return;
    }

    if (s_state == SYSID_RECOVERY) {
        s_rate_cmd = 0.0f;
        s_t_recover += SYSID_DT;
        if (s_t_recover >= SYSID_RECOVERY_T) sysid_finish();
        return;
    }

    // Active states (RAMP_IN / RUNNING / RAMP_OUT): check aborts first.
    if (sysid_abort_condition()) {
        sysid_enter_recovery();
        return;
    }

    s_t_sig += SYSID_DT;
    env = sysid_envelope();         // also sets s_state among RAMP_IN/RUNNING/RAMP_OUT
    if (env < 0.0f) {               // window complete -> graceful finish
        sysid_finish();
        return;
    }
    raw = sysid_signal_raw();
    s_rate_cmd = env * s_amp * raw; // mean-zero excitation in the axis's rate unit
}

uint8_t SysID_IsAxisActive(SysID_Axis_e axis)
{
    if (axis != s_axis) return 0;
    return (s_state == SYSID_RAMP_IN || s_state == SYSID_RUNNING || s_state == SYSID_RAMP_OUT) ? 1U : 0U;
}

float SysID_GetRateSetpoint(SysID_Axis_e axis)
{
    (void)axis;
    return s_rate_cmd;
}

float SysID_GetDither(void)
{
    return s_rate_cmd; // raw excitation; 0 in IDLE/RECOVERY (sysid_finish/enter_recovery clear it)
}

uint8_t SysID_GetAxis(void)
{
    return (uint8_t)s_axis;
}

uint8_t SysID_GetState(void)
{
    return (uint8_t)s_state;
}

void SysID_SetGeofence(uint8_t enabled)
{
    s_geofence_en = enabled ? 1U : 0U;
}
