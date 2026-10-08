/* Host driver for the vp table in TASK/StabilizerTask.c (test_vp_table_host.py). The test copies the block from
 * "MRAC variant rows" to the end of Keil_VariantPoll into vp_rows.inc; this file stubs what that block uses,
 * applies every row and the user row on the ground, and prints what each one left in the MRAC config. */
#include <stdio.h>
#include <stdint.h>
#include "mrac.h"
#include "mrac_variant.h"

typedef int FlightState_t;
_imu_st imu_data = {0.0f, 0.0f};
volatile uint8_t vp_id = 0U, vp_active = 0U;
static uint8_t s_vp_last = 0U;
static uint8_t Keil_OnGround(FlightState_t s) { (void)s; return 1U; }

#include "vp_rows.inc"

int main(void)
{
	unsigned i, n = (unsigned)(sizeof(s_vp) / sizeof(s_vp[0]));
	MRAC_Init();
	for (i = 0U; i <= n; i++) {
		const VariantPreset_t *r = (i == n) ? &vp_user : &s_vp[i];
		float g_rbf = -1.0f;
		vp_id = (uint8_t)((i == n) ? VP_USER_ID : i);
		if (i == n) vp_user_go = 1U;
		Keil_VariantPoll(0);
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
		g_rbf = mrac_g_gamma[0][MRAC_GRP_RBF];
#endif
		printf("row %u %u %u %g %g %g %g %g %g %g %g %u %g\n", (unsigned)vp_id, (unsigned)vp_active, (unsigned)r->basis,
		       mrac_config_pitch.gamma_c, mrac_config_pitch.b_axis, mrac_config_pitch.pe_delay, mrac_config_pitch.wc_pe,
		       mrac_config_pitch.p_max, mrac_g_gamma[0][0], mrac_g_gamma[2][0], g_rbf, (unsigned)r->te_off,
		       mrac_config_pitch.p_forget);
	}
	return 0;
}
