/* Host driver for API/pid.c ComputePID (WP-10 golden test of cascade.Pid).
 * stdin: blocks of "Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin n" followed by n "Des FB" pairs.
 * stdout: one "E SumE Up Ui Ud U" line per ComputePID call, state reset per block, AW_LEGACY. */
#include <stdio.h>
#include <stdlib.h>
#include "pid.h"

float Sin_Yaw = 0.0f, Cos_Yaw = 1.0f, Sin_Pitch = 0.0f, Cos_Pitch = 1.0f, Sin_Roll = 0.0f, Cos_Roll = 1.0f;

static int next_float(float *out)
{
	char tok[64];
	char *end;
	if (scanf("%63s", tok) != 1)
		return 0;
	*out = strtof(tok, &end);
	return *end == '\0';
}

int main(void)
{
	float *row[9];
	float n, des, fb;
	int i, k;
	for (;;)
	{
		PIDTypeDef p = {0};
		row[0] = &p.Kp; row[1] = &p.Ki; row[2] = &p.Kd; row[3] = &p.UMax; row[4] = &p.UpMax;
		row[5] = &p.UiMax; row[6] = &p.UdMax; row[7] = &p.SumEMax; row[8] = &p.EMin;
		for (k = 0; k < 9; k++)
			if (!next_float(row[k]))
				return k == 0 ? 0 : 1;
		if (!next_float(&n))
			return 1;
		p.aw_mode = AW_LEGACY;
		for (i = 0; i < (int)n; i++)
		{
			if (!next_float(&des) || !next_float(&fb))
				return 1;
			p.Des = des;
			p.FB = fb;
			ComputePID(&p);
			printf("%.9g %.9g %.9g %.9g %.9g %.9g\n", p.E, p.SumE, p.Up, p.Ui, p.Ud, p.U);
		}
	}
}
