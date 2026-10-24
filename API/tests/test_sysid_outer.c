/* Host test: sysid outer loops targets 2 and 3 (VAR_WRITE 55 sysid_target), API/sysid.c.
 *
 * Target 2 (angle setpoint injection): pitch/roll only.
 * Target 3 (velocity setpoint injection): x/y/z.
 * Z guard: Z axis is only allowed on target 3.
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
	Ctrler.Z_posPID.FB = 0.80f;
	Ctrler.locxPID.FB = 0.0f;
	Ctrler.locyPID.FB = 0.0f;
	Ctrler.pitchPID.FB = 0.0f;
	Ctrler.rollPID.FB = 0.0f;
	s_rc_active = 0;
}

static float run_peak_angle(SysID_Axis_e ax, int max_ticks)
{
	float peak = 0.0f;
	int i;
	for (i = 0; i < max_ticks && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		float d;
		SysID_Update();
		d = fabsf(SysID_GetAngleSetpoint(ax));
		if (d > peak) peak = d;
	}
	return peak;
}

static float run_peak_vel(SysID_Axis_e ax, int max_ticks)
{
	float peak = 0.0f;
	int i;
	for (i = 0; i < max_ticks && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		float d;
		SysID_Update();
		d = fabsf(SysID_GetVelSetpoint(ax));
		if (d > peak) peak = d;
	}
	return peak;
}

int main(void)
{
	static const float amp_max_angle = 30.0f; // PROPOSED
	static const float amp_max_vel_xy = 100.0f; // PROPOSED
	static const float amp_max_vel_z = 0.5f; // PROPOSED
	float peak;
	int i;

	/* Target 2: angle setpoint injection. Z rejected. */
	hover();
	sysid_target = 2U;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 10.0f, 1.0f) == 0U, "target 2 Start Z accepted");

	/* Valid target 2 run on roll */
	hover();
	CHECK(SysID_Start(SYSID_AXIS_ROLL, SYSID_SIG_MULTISINE, 0.5f, 5.0f, 50.0f, 2.0f) == 1U, "target 2 Start roll rejected");
	peak = 0.0f;
	for (i = 0; i < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		float d;
		SysID_Update();
		d = fabsf(SysID_GetAngleSetpoint(SYSID_AXIS_ROLL));
		if (d > peak) peak = d;
		CHECK(SysID_GetVelSetpoint(SYSID_AXIS_ROLL) == 0.0f, "target 2 vel not 0");
		CHECK(SysID_GetRateSetpoint(SYSID_AXIS_ROLL) == 0.0f, "target 2 rate not 0");
		CHECK(SysID_GetTorque(SYSID_AXIS_ROLL, 1U) == 0.0f, "target 2 torque not 0");
	}
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 2 roll did not end");
	CHECK(peak <= amp_max_angle && peak > 0.5f * amp_max_angle, "target 2 peak |d| %g vs amp_max_angle %g", (double)peak, (double)amp_max_angle);

	/* Target 3: velocity setpoint injection. Z accepted. */
	hover();
	sysid_target = 3U;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 10.0f, 2.0f) == 1U, "target 3 Start Z rejected");
	peak = 0.0f;
	for (i = 0; i < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		float d;
		SysID_Update();
		d = fabsf(SysID_GetVelSetpoint(SYSID_AXIS_Z));
		if (d > peak) peak = d;
		CHECK(SysID_GetAngleSetpoint(SYSID_AXIS_Z) == 0.0f, "target 3 angle not 0");
		CHECK(SysID_GetRateSetpoint(SYSID_AXIS_Z) == 0.0f, "target 3 rate not 0");
	}
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 3 Z did not end");
	CHECK(peak <= amp_max_vel_z && peak > 0.5f * amp_max_vel_z, "target 3 Z peak |d| %g vs amp_max_vel_z %g", (double)peak, (double)amp_max_vel_z);

	/* Target 3: XY test */
	hover();
	sysid_target = 3U;
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_MULTISINE, 0.5f, 5.0f, 200.0f, 2.0f) == 1U, "target 3 Start PITCH rejected");
	peak = 0.0f;
	for (i = 0; i < 1500 && SysID_GetState() != (uint8_t)SYSID_IDLE; i++) {
		float d;
		SysID_Update();
		d = fabsf(SysID_GetVelSetpoint(SYSID_AXIS_PITCH));
		if (d > peak) peak = d;
	}
	CHECK(SysID_GetState() == (uint8_t)SYSID_IDLE, "target 3 pitch did not end");
	CHECK(peak <= amp_max_vel_xy && peak > 0.5f * amp_max_vel_xy, "target 3 XY peak |d| %g vs amp_max_vel_xy %g", (double)peak, (double)amp_max_vel_xy);

	/* Target 2 abort -> RECOVERY, d 0 */
	hover();
	sysid_target = 2U;
	CHECK(SysID_Start(SYSID_AXIS_PITCH, SYSID_SIG_CHIRP, 0.5f, 5.0f, 20.0f, 5.0f) == 1U, "abort run Start rejected");
	for (i = 0; i < 600; i++) SysID_Update();
	SysID_Abort();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "Abort did not enter RECOVERY");
	peak = run_peak_angle(SYSID_AXIS_PITCH, 1000);
	CHECK(peak == 0.0f, "angle %g after Abort", (double)peak);

	/* Z guard: Target 3 Z abort below altitude */
	hover();
	sysid_target = 3U;
	CHECK(SysID_Start(SYSID_AXIS_Z, SYSID_SIG_CHIRP, 0.5f, 5.0f, 0.2f, 5.0f) == 1U, "Z abort start rejected");
	for (i = 0; i < 600; i++) SysID_Update();
	Ctrler.Z_posPID.FB = 0.2f; /* Below SYSID_ALT_MIN_M 0.30f */
	SysID_Update();
	CHECK(SysID_GetState() == (uint8_t)SYSID_RECOVERY, "Z altitude trip did not enter RECOVERY");
	CHECK(SysID_GetVelSetpoint(SYSID_AXIS_Z) == 0.0f, "vel after altitude trip");
	Ctrler.Z_posPID.FB = 0.8f;
	peak = run_peak_vel(SYSID_AXIS_Z, 1000);
	CHECK(peak == 0.0f, "vel %g during recovery", (double)peak);

	if (g_fail) { printf("test_sysid_outer: %d FAILED\n", g_fail); return 1; }
	printf("test_sysid_outer: all passed\n");
	return 0;
}
