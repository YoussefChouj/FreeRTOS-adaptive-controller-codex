/* Host driver for API/xy_ctrl.c (test_xy_ctrl_host.py): one row per tick on stdin,
 * "op kind des pos vel vff u_commit" with op s = Step, t = Track; prints the Step output (0 for Track),
 * then commits u_commit (the Python controller's command, so both pipes hold the same history). */
#include <stdio.h>
#include "xy_ctrl.h"

int main(void)
{
	XyCtrlState s;
	char op;
	unsigned kind;
	float des, pos, vel, vff, uc;
	XyCtrl_Reset(&s);
	while (scanf(" %c %u %f %f %f %f %f", &op, &kind, &des, &pos, &vel, &vff, &uc) == 7) {
		float u = 0.0f;
		if (op == 's')
			u = XyCtrl_Step(&s, (uint8_t)kind, des, pos, vel, vff);
		else
			XyCtrl_Track(&s, (uint8_t)kind, pos);
		XyCtrl_Commit(&s, uc);
		printf("%.9g\n", u);
	}
	return 0;
}
