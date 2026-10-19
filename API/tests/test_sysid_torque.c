/* Host test: sysid excitation target (VAR_WRITE 55 sysid_target), API/sysid.c.
 *
 * Target 0 (rate setpoint injection) is unchanged: SysID_GetTorque is 0 on every axis and
 * SysID_IsAxisActive drives the rate setpoint. Target 1 (torque add before the mixer, u = c + d):
 * d only on the excited axis, 0 when not flying, 0 after an abort (RECOVERY) and after the run, |d| at
 * most the SYSID_TORQUE_ROW amp_max, SysID_IsAxisActive 0. The target is latched at SysID_Start.
 * Start rejects Z on both targets.
 *
 * One translation unit: the firmware headers sysid.c includes are blocked by their include guards and
 * the few globals it reads are defined here, then ../sysid.c is included.
 *
 * Build: gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement API/tests/test_sysid_torque.c -lm
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
	Ctrler.Z_posPID.FB = 0.80f;
	Ctrler.locxPID.FB = 0.0f;
	Ctrler.locyPID.FB = 0.0f;
	Ctrler.pitchPID.FB = 0.0f;
	Ctrler.rollPID.FB = 0.0f;
	s_rc_active = 0;
}

/* Tick until IDLE (or max ticks); return the peak |d| seen on axis `ax` with flying = 1. */
static float run_peak_torque(SysID_Axis_e ax, int max_ticks)
{
	float peak = 0.0f;
	int i;
	for (i = 0; i < max_ticks && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		float d;
		SysID_Update();
		d = fabsf(SysID_GetTorque(ax, 1U));
		if (d > peak) peak = d;
	}
	return peak;
}

int main(void)
{
	static const float amp_max[3] = { 50.0f, 50.0f, 40.0f };   /* SYSID_TORQUE_ROW pitch/roll/yaw */
	float peak_rate = 0.0f;
	float peak;
	int i, k, ax;

	/* Target 0: torque path silent, rate injection drives the axis. */
	hover();
	sysid_target = 0U;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 10.0f, 1.0f) == 0U, "target 0 Start Z accepted");
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 10.0f, 1.0f) == 1U, "target 0 Start rejected");
	for (i = 0; i < 1200 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		SysID_Update();
		for (k = 0; k < 4; k++) CHECK(SysID_GetTorque((SysID_Axis_e)k, 1U) == 0.0f, "target 0 torque on axis %d", k);
		if (SysID_GetState() == (uint8_t)SYSID_RUNNING) {
			CHECK(SysID_IsAxisActive(SYSID_AXIS_PITCH) == 1U, "target 0 pitch not active while RUNNING");
			if (fabsf(SysID_GetRateSetpoint(SYSID_AXIS_PITCH)) > peak_rate) peak_rate = fabsf(SysID_GetRateSetpoint(SYSID_AXIS_PITCH));
		}
	}
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 0 run did not end");
	CHECK(peak_rate > 5.0f, "target 0 rate injection peak %g", (double)peak_rate);
	printf("target 0: torque 0 on all axes, rate setpoint peak %.4f deg/s (amp 10)\n", (double)peak_rate);

	/* Target 1: Z still rejected. */
	sysid_target = 1U;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 10.0f, 1.0f) == 0U, "target 1 Start Z accepted");

	/* Target 1 per axis, amp far over the row: |d| <= amp_max and live, other axes 0, IsAxisActive 0. */
	for (ax = 0; ax < 3; ax++) {
		float other_max = 0.0f;
		hover();
		CHECK(SysID_Start((SysID_Axis_e)ax, SYSID_SIG_MULTISINE, 0.5f, 8.0f, 1000.0f, 2.0f) == 1U, "target 1 Start axis %d rejected", ax);
		peak = 0.0f;
		for (i = 0; i < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
			float d;
			SysID_Update();
			d = fabsf(SysID_GetTorque((SysID_Axis_e)ax, 1U));
			if (d > peak) peak = d;
			for (k = 0; k < 4; k++) {
				if (k != ax && fabsf(SysID_GetTorque((SysID_Axis_e)k, 1U)) > other_max) other_max = fabsf(SysID_GetTorque((SysID_Axis_e)k, 1U));
			}
			CHECK(SysID_GetTorque((SysID_Axis_e)ax, 0U) == 0.0f, "target 1 axis %d torque while not flying", ax);
			CHECK(SysID_IsAxisActive((SysID_Axis_e)ax) == 0U, "target 1 axis %d rate injection active", ax);
			CHECK(SysID_GetRateSetpoint((SysID_Axis_e)ax) == SysID_GetRateSetpoint((SysID_Axis_e)ax), "nan");
		}
		CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 1 axis %d run did not end", ax);
		CHECK(peak <= amp_max[ax] && peak > 0.5f * amp_max[ax], "target 1 axis %d peak |d| %g vs amp_max %g", ax, (double)peak, (double)amp_max[ax]);
		CHECK(other_max == 0.0f, "target 1 axis %d: other axis torque %g", ax, (double)other_max);
		CHECK(SysID_GetTorque((SysID_Axis_e)ax, 1U) == 0.0f, "target 1 axis %d torque after the run", ax);
		printf("target 1 axis %d: peak |d| %.4f (amp_max %.0f), other axes 0\n", ax, (double)peak, (double)amp_max[ax]);
	}

	/* Target 1 abort: SysID_Abort -> RECOVERY, d 0 from that tick on. */
	hover();
	CHECK(SysID_Start(SYSID_AXIS_ROLL, SYSID_SIG_CHIRP, 0.5f, 5.0f, 30.0f, 5.0f) == 1U, "abort run Start rejected");
	for (i = 0; i < 600; i++) SysID_Update();   /* past the 1.5 s ramp-in */
	CHECK(SysID_GetState() == (uint8_t)SYSID_RUNNING, "abort run not RUNNING");
	SysID_Abort();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "Abort did not enter RECOVERY");
	peak = run_peak_torque(SYSID_AXIS_ROLL, 1000);
	CHECK(peak == 0.0f, "torque %g after Abort", (double)peak);

	/* Target 1 auto-abort (attitude over the 30 deg limit) -> RECOVERY, d 0. */
	hover();
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 30.0f, 5.0f) == 1U, "trip run Start rejected");
	for (i = 0; i < 600; i++) SysID_Update();
	Ctrler.pitchPID.FB = 35.0f;
	SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "attitude trip did not enter RECOVERY");
	CHECK(SysID_GetTorque(SYSID_AXIS_PITCH, 1U) == 0.0f, "torque after attitude trip");
	Ctrler.pitchPID.FB = 0.0f;
	peak = run_peak_torque(SYSID_AXIS_PITCH, 1000);
	CHECK(peak == 0.0f, "torque %g during recovery", (double)peak);

	/* Latch: a mid-run sysid_target write (the firmware refuses it BUSY; written raw here) changes nothing. */
	hover();
	sysid_target = 1U;
	CHECK(SysID_Start(SYSID_AXIS_YAW, SYSID_SIG_CHIRP, 0.5f, 5.0f, 30.0f, 3.0f) == 1U, "latch run Start rejected");
	for (i = 0; i < 600; i++) SysID_Update();
	sysid_target = 0U;
	peak = 0.0f;
	for (i = 0; i < 200; i++) {
		SysID_Update();
		if (fabsf(SysID_GetTorque(SYSID_AXIS_YAW, 1U)) > peak) peak = fabsf(SysID_GetTorque(SYSID_AXIS_YAW, 1U));
		CHECK(SysID_IsAxisActive(SYSID_AXIS_YAW) == 0U, "latch: rate injection switched on mid-run");
	}
	CHECK(peak > 0.0f, "latch: torque stopped after the mid-run write");
	SysID_Abort();
	(void)run_peak_torque(SYSID_AXIS_YAW, 1000);
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "latch run did not recover to IDLE");

	if (g_fail) { printf("test_sysid_torque: %d FAILED\n", g_fail); return 1; }
	printf("test_sysid_torque: all passed\n");
	return 0;
}
