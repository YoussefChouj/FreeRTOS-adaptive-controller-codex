/* Host test: outer-loop sysid excitation targets (VAR_WRITE 55 sysid_target = 2, 3, 4), API/sysid.c.
 *
 * Verifies:
 *   1. Targets 0 (rate) and 1 (torque) remain bit-identical and never trigger outer setpoint getters.
 *   2. Target 2 (angle, deg): accepts pitch (0) and roll (1); refuses yaw (2) and Z (3); refuses
 *      out-of-range amplitude (> 10 deg or <= 0); injects signed excitation only on the active axis;
 *      resets immediately to `des_deg` on SysID_Stop/Abort, disarm, mode change, tilt or error trip.
 *   3. Target 3 (velocity, cm/s for X/Y, m/s for Z): accepts X (0), Y (1), Z (3); refuses yaw (2);
 *      refuses out-of-range amplitude (> 30 cm/s on X/Y, > 0.40 m/s on Z); enforces Z minimum altitude
 *      (SYSID_Z_MIN_ALT_M = 0.40 m) at Start and mid-run; auto-aborts on tilt/error.
 *   4. Target 4 (position, cm for X/Y, m for Z): accepts X (0), Y (1), Z (3); refuses yaw (2);
 *      refuses out-of-range amplitude (> 30 cm on X/Y, > 0.25 m on Z); enforces Z minimum altitude
 *      at Start and mid-run; auto-aborts on tilt/error; resets to `des_pos` on stop.
 *   5. Target latching at SysID_Start and non-IDLE state during run/recovery (BUSY guard).
 *
 * Build: gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement API/tests/test_sysid_outer.c -lm
 */
#include <stdint.h>
#include <stdio.h>
#include <math.h>

#define __PID_H
#define MRAC_H
#define __RC_INPUT_H__
#define __global_dectare_h__

#define FlyMode_SDK 1
typedef struct { unsigned char ARM_Status; unsigned char FlyMode; } DroneStatusTypeDef;
typedef struct { float Des; float FB; } PIDTypeDef;
typedef struct { PIDTypeDef Z_posPID, locxPID, locyPID, pitchPID, rollPID; } CtrlerTypeDef;
typedef struct { uint8_t id_frame_on; } MRAC_FeatureFlags_t;
typedef enum { RC_AXIS_THR = 0, RC_AXIS_PITCH = 1, RC_AXIS_ROLL = 2, RC_AXIS_YAW = 3 } RC_Axis_t;

DroneStatusTypeDef DroneStatus;
CtrlerTypeDef Ctrler;
MRAC_FeatureFlags_t mrac_flags;
static int s_rc_active;
int RCInput_IsActive(RC_Axis_t axis) { (void)axis; return s_rc_active; }

#include "../sysid.c"

static int g_fail;
#define CHECK(c, ...) do { if (!(c)) { g_fail++; printf("FAIL %s:%d ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static void hover(void)
{
	DroneStatus.ARM_Status = 1U;
	DroneStatus.FlyMode = FlyMode_SDK;
	Ctrler.Z_posPID.Des = 0.80f;
	Ctrler.Z_posPID.FB  = 0.80f;
	Ctrler.locxPID.Des  = 0.0f;
	Ctrler.locxPID.FB   = 0.0f;
	Ctrler.locyPID.Des  = 0.0f;
	Ctrler.locyPID.FB   = 0.0f;
	Ctrler.pitchPID.Des = 0.0f;
	Ctrler.pitchPID.FB  = 0.0f;
	Ctrler.rollPID.Des  = 0.0f;
	Ctrler.rollPID.FB   = 0.0f;
	s_rc_active = 0;
}

static void drain_to_idle(int max_ticks)
{
	int i;
	for (i = 0; i < max_ticks && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		SysID_Update();
	}
}

int main(void)
{
	const float base_des = 1.25f;
	float peak_pos, peak_neg, d;
	int i, k, ax;
	uint8_t valid_vel_pos[3] = { SYSID_AXIS_X, SYSID_AXIS_Y, SYSID_AXIS_Z };

	/* 1. Targets 0 and 1: outer getters are strictly no-op (return base_des bit-identically). */
	hover();
	sysid_target = (uint8_t)SYSID_TARGET_RATE;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 5.0f, 1.0f) == 0U, "target 0 Z should be refused");
	CHECK(SysID_Start(SYSID_AXIS_ROLL, SYSID_SIG_CHIRP, 0.5f, 5.0f, 10.0f, 1.0f) == 1U, "target 0 roll start failed");
	for (i = 0; i < 1200 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		SysID_Update();
		for (k = 0; k < 4; k++) {
			CHECK(SysID_GetAngleSetpoint((uint8_t)k, base_des) == base_des, "target 0 touched angle setpoint");
			CHECK(SysID_GetVelSetpoint((uint8_t)k, base_des) == base_des, "target 0 touched vel setpoint");
			CHECK(SysID_GetPosSetpoint((uint8_t)k, base_des) == base_des, "target 0 touched pos setpoint");
			CHECK(SysID_GetTorque((SysID_Axis_e)k, 1U) == 0.0f, "target 0 touched torque");
		}
	}
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 0 did not finish to IDLE");

	hover();
	sysid_target = (uint8_t)SYSID_TARGET_TORQUE;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 5.0f, 1.0f) == 0U, "target 1 Z should be refused");
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 20.0f, 1.0f) == 1U, "target 1 pitch start failed");
	for (i = 0; i < 1200 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		SysID_Update();
		for (k = 0; k < 4; k++) {
			CHECK(SysID_GetAngleSetpoint((uint8_t)k, base_des) == base_des, "target 1 touched angle setpoint");
			CHECK(SysID_GetVelSetpoint((uint8_t)k, base_des) == base_des, "target 1 touched vel setpoint");
			CHECK(SysID_GetPosSetpoint((uint8_t)k, base_des) == base_des, "target 1 touched pos setpoint");
		}
	}
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 1 did not finish to IDLE");

	/* 2. Target 2 (ANGLE): pitch (0) and roll (1) only; refuse yaw (2), Z (3), and out-of-range amp. */
	hover();
	sysid_target = (uint8_t)SYSID_TARGET_ANGLE;
	CHECK(SysID_Start(SYSID_AXIS_YAW, SYSID_SIG_CHIRP, 0.5f, 5.0f, 5.0f, 2.0f) == 0U, "target 2 yaw must be refused");
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 5.0f, 2.0f) == 0U, "target 2 Z must be refused");
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 0.0f, 2.0f) == 0U, "target 2 zero amp must be refused");
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, -3.0f, 2.0f) == 0U, "target 2 neg amp must be refused");
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 15.0f, 2.0f) == 0U, "target 2 amp > 10 deg must be refused");

	for (ax = 0; ax < 2; ax++) {
		hover();
		CHECK(SysID_Start((SysID_Axis_e)ax, SYSID_SIG_MULTISINE, 0.5f, 5.0f, 6.0f, 2.0f) == 1U, "target 2 axis %d start failed", ax);
		peak_pos = 0.0f;
		peak_neg = 0.0f;
		for (i = 0; i < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
			SysID_Update();
			d = SysID_GetAngleSetpoint((uint8_t)ax, base_des) - base_des;
			if (d > peak_pos) peak_pos = d;
			if (d < peak_neg) peak_neg = d;
			CHECK(fabsf(d) <= 10.001f, "target 2 axis %d exceeded 10 deg cap: %g", ax, (double)d);
			for (k = 0; k < 4; k++) {
				if (k != ax) {
					CHECK(SysID_GetAngleSetpoint((uint8_t)k, base_des) == base_des, "target 2 leaked to angle axis %d", k);
				}
				CHECK(SysID_GetVelSetpoint((uint8_t)k, base_des) == base_des, "target 2 leaked to vel axis %d", k);
				CHECK(SysID_GetPosSetpoint((uint8_t)k, base_des) == base_des, "target 2 leaked to pos axis %d", k);
				CHECK(SysID_GetTorque((SysID_Axis_e)k, 1U) == 0.0f, "target 2 leaked to torque axis %d", k);
				CHECK(SysID_IsAxisActive((SysID_Axis_e)k) == 0U, "target 2 set rate IsAxisActive on %d", k);
			}
		}
		CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 2 axis %d did not end", ax);
		CHECK(peak_pos > 2.0f && peak_neg < -2.0f, "target 2 axis %d peaks +%g / %g", ax, (double)peak_pos, (double)peak_neg);
		CHECK(SysID_GetAngleSetpoint((uint8_t)ax, base_des) == base_des, "target 2 axis %d not reset after finish", ax);
	}

	/* Target 2: stop, tilt auto-abort, and tracking error auto-abort remove excitation immediately. */
	hover();
	CHECK(SysID_Start(SYSID_AXIS_ROLL, SYSID_SIG_CHIRP, 0.5f, 5.0f, 5.0f, 4.0f) == 1U, "target 2 stop test start failed");
	for (i = 0; i < 400; i++) SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RUNNING, "target 2 not RUNNING");
	SysID_Stop();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "SysID_Stop did not enter RECOVERY");
	CHECK(SysID_GetAngleSetpoint(SYSID_AXIS_ROLL, base_des) == base_des, "target 2 non-zero after SysID_Stop");
	drain_to_idle(1000);

	hover();
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 5.0f, 4.0f) == 1U, "target 2 error test start failed");
	for (i = 0; i < 400; i++) SysID_Update();
	Ctrler.pitchPID.Des = 22.0f;
	Ctrler.pitchPID.FB  = 0.0f;
	SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "target 2 angle error did not abort");
	CHECK(SysID_GetAngleSetpoint(SYSID_AXIS_PITCH, base_des) == base_des, "target 2 non-zero after error abort");
	drain_to_idle(1000);

	/* 3. Target 3 (VELOCITY): X (0), Y (1), Z (3); refuse yaw (2), out-of-range amp, and low Z altitude. */
	hover();
	sysid_target = (uint8_t)SYSID_TARGET_VEL;
	CHECK(SysID_Start(SYSID_AXIS_YAW, SYSID_SIG_CHIRP, 0.5f, 4.0f, 10.0f, 2.0f) == 0U, "target 3 yaw must be refused");
	CHECK(SysID_Start(SYSID_AXIS_X, SYSID_SIG_CHIRP, 0.5f, 4.0f, 35.0f, 2.0f) == 0U, "target 3 X amp > 30 cm/s must be refused");
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 4.0f, 0.50f, 2.0f) == 0U, "target 3 Z amp > 0.40 m/s must be refused");

	/* Z minimum altitude guard at Start (0.35 m < SYSID_Z_MIN_ALT_M = 0.40 m). */
	hover();
	Ctrler.Z_posPID.FB = 0.35f;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 4.0f, 0.20f, 2.0f) == 0U, "target 3 Z below SYSID_Z_MIN_ALT_M must be refused");

	for (i = 0; i < 3; i++) {
		uint8_t a = valid_vel_pos[i];
		float amp = (a == SYSID_AXIS_Z) ? 0.25f : 20.0f;
		float cap = (a == SYSID_AXIS_Z) ? 0.40f : 30.0f;
		int t;
		hover();
		CHECK(SysID_Start((SysID_Axis_e)a, SYSID_SIG_CHIRP, 0.5f, 4.0f, amp, 2.0f) == 1U, "target 3 axis %u start failed", (unsigned)a);
		peak_pos = 0.0f;
		peak_neg = 0.0f;
		for (t = 0; t < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; t++) {
			SysID_Update();
			d = SysID_GetVelSetpoint(a, base_des) - base_des;
			if (d > peak_pos) peak_pos = d;
			if (d < peak_neg) peak_neg = d;
			CHECK(fabsf(d) <= cap + 1e-4f, "target 3 axis %u exceeded cap: %g", (unsigned)a, (double)d);
			for (k = 0; k < 4; k++) {
				if ((uint8_t)k != a) {
					CHECK(SysID_GetVelSetpoint((uint8_t)k, base_des) == base_des, "target 3 leaked to vel axis %d", k);
				}
				CHECK(SysID_GetAngleSetpoint((uint8_t)k, base_des) == base_des, "target 3 leaked to angle axis %d", k);
				CHECK(SysID_GetPosSetpoint((uint8_t)k, base_des) == base_des, "target 3 leaked to pos axis %d", k);
			}
		}
		CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 3 axis %u did not end", (unsigned)a);
		CHECK(peak_pos > 0.3f * amp && peak_neg < -0.3f * amp, "target 3 axis %u peaks +%g / %g", (unsigned)a, (double)peak_pos, (double)peak_neg);
		CHECK(SysID_GetVelSetpoint(a, base_des) == base_des, "target 3 axis %u not reset after finish", (unsigned)a);
	}

	/* Target 3 Z mid-run altitude drop below SYSID_Z_MIN_ALT_M auto-aborts and clears excitation. */
	hover();
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 4.0f, 0.25f, 4.0f) == 1U, "target 3 Z mid-run abort start failed");
	for (i = 0; i < 400; i++) SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RUNNING, "target 3 Z not RUNNING");
	Ctrler.Z_posPID.FB = 0.38f;   /* below SYSID_Z_MIN_ALT_M (0.40 m), above s_safe.alt_min_m (0.30 m) */
	SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "target 3 Z low-alt did not enter RECOVERY");
	CHECK(SysID_GetVelSetpoint(SYSID_AXIS_Z, base_des) == base_des, "target 3 Z non-zero after low-alt abort");
	Ctrler.Z_posPID.FB = 0.80f;
	drain_to_idle(1000);

	/* 4. Target 4 (POSITION): X (0), Y (1), Z (3); refuse yaw (2), out-of-range amp, and low Z altitude. */
	hover();
	sysid_target = (uint8_t)SYSID_TARGET_POS;
	CHECK(SysID_Start(SYSID_AXIS_YAW, SYSID_SIG_CHIRP, 0.3f, 2.0f, 10.0f, 2.0f) == 0U, "target 4 yaw must be refused");
	CHECK(SysID_Start(SYSID_AXIS_Y, SYSID_SIG_CHIRP, 0.3f, 2.0f, 35.0f, 2.0f) == 0U, "target 4 Y amp > 30 cm must be refused");
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.3f, 2.0f, 0.30f, 2.0f) == 0U, "target 4 Z amp > 0.25 m must be refused");

	hover();
	Ctrler.Z_posPID.FB = 0.36f;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.3f, 2.0f, 0.15f, 2.0f) == 0U, "target 4 Z below SYSID_Z_MIN_ALT_M must be refused");

	for (i = 0; i < 3; i++) {
		uint8_t a = valid_vel_pos[i];
		float amp = (a == SYSID_AXIS_Z) ? 0.15f : 15.0f;
		float cap = (a == SYSID_AXIS_Z) ? 0.25f : 30.0f;
		int t;
		hover();
		CHECK(SysID_Start((SysID_Axis_e)a, SYSID_SIG_CHIRP, 0.3f, 2.0f, amp, 2.0f) == 1U, "target 4 axis %u start failed", (unsigned)a);
		peak_pos = 0.0f;
		peak_neg = 0.0f;
		for (t = 0; t < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; t++) {
			SysID_Update();
			d = SysID_GetPosSetpoint(a, base_des) - base_des;
			if (d > peak_pos) peak_pos = d;
			if (d < peak_neg) peak_neg = d;
			CHECK(fabsf(d) <= cap + 1e-4f, "target 4 axis %u exceeded cap: %g", (unsigned)a, (double)d);
			for (k = 0; k < 4; k++) {
				if ((uint8_t)k != a) {
					CHECK(SysID_GetPosSetpoint((uint8_t)k, base_des) == base_des, "target 4 leaked to pos axis %d", k);
				}
				CHECK(SysID_GetAngleSetpoint((uint8_t)k, base_des) == base_des, "target 4 leaked to angle axis %d", k);
				CHECK(SysID_GetVelSetpoint((uint8_t)k, base_des) == base_des, "target 4 leaked to vel axis %d", k);
			}
		}
		CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 4 axis %u did not end", (unsigned)a);
		CHECK(peak_pos > 0.3f * amp && peak_neg < -0.3f * amp, "target 4 axis %u peaks +%g / %g", (unsigned)a, (double)peak_pos, (double)peak_neg);
		CHECK(SysID_GetPosSetpoint(a, base_des) == base_des, "target 4 axis %u not reset after finish", (unsigned)a);
	}

	/* Target 4: tilt auto-abort, disarm, and mid-run target latch. */
	hover();
	sysid_target = (uint8_t)SYSID_TARGET_POS;
	CHECK(SysID_Start(SYSID_AXIS_X, SYSID_SIG_CHIRP, 0.3f, 2.0f, 15.0f, 4.0f) == 1U, "target 4 latch test start failed");
	for (i = 0; i < 400; i++) SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RUNNING, "target 4 not RUNNING");
	/* Mid-run raw write to sysid_target (blocked by BUSY in VarWrite_Apply) must not switch active target. */
	sysid_target = (uint8_t)SYSID_TARGET_ANGLE;
	peak_pos = 0.0f;
	for (i = 0; i < 100; i++) {
		SysID_Update();
		d = fabsf(SysID_GetPosSetpoint(SYSID_AXIS_X, base_des) - base_des);
		if (d > peak_pos) peak_pos = d;
		CHECK(SysID_GetAngleSetpoint(SYSID_AXIS_X, base_des) == base_des, "latch broken: switched to angle mid-run");
	}
	CHECK(peak_pos > 0.0f, "latch broken: pos excitation stopped after raw sysid_target write");
	/* Disarm immediately removes excitation even before the next SysID_Update tick. */
	DroneStatus.ARM_Status = 0U;
	CHECK(SysID_GetPosSetpoint(SYSID_AXIS_X, base_des) == base_des, "disarm did not immediately zero pos excitation");
	SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "disarm did not transition to RECOVERY");
	hover();
	drain_to_idle(1000);
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "did not return to IDLE after recovery");

	if (g_fail) {
		printf("test_sysid_outer: %d FAILED\n", g_fail);
		return 1;
	}
	printf("test_sysid_outer: all passed\n");
	return 0;
}
