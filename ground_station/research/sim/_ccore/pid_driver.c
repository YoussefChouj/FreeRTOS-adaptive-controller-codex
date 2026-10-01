/* Host driver for API/pid.c ComputePID (WP-10 golden test of cascade.Pid).
 * stdin: blocks of "Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin n" followed by n "Des FB" pairs.
 * stdout: one "E SumE Up Ui Ud U" line per ComputePID call, state reset per block, AW_LEGACY. */
#include <stdio.h>
#include "pid.h"

float Sin_Yaw = 0.0f, Cos_Yaw = 1.0f, Sin_Pitch = 0.0f, Cos_Pitch = 1.0f, Sin_Roll = 0.0f, Cos_Roll = 1.0f;

int main(void)
{
	PIDTypeDef p;
	int i, n;
	float des, fb;
	for (;;)
	{
		PIDTypeDef zero = {0};
		p = zero;
		if (scanf("%f %f %f %f %f %f %f %f %f %d", &p.Kp, &p.Ki, &p.Kd, &p.UMax, &p.UpMax, &p.UiMax, &p.UdMax,
		          &p.SumEMax, &p.EMin, &n) != 10)
			return 0;
		p.aw_mode = AW_LEGACY;
		for (i = 0; i < n; i++)
		{
			if (scanf("%f %f", &des, &fb) != 2)
				return 1;
			p.Des = des;
			p.FB = fb;
			ComputePID(&p);
			printf("%.9g %.9g %.9g %.9g %.9g %.9g\n", p.E, p.SumE, p.Up, p.Ui, p.Ud, p.U);
		}
	}
}
