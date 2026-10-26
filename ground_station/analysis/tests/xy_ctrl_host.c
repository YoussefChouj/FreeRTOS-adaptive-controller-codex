/* Host driver for API/xy_ctrl.c (test_xy_ctrl_host.py): one row per tick on stdin,
 *   "s|t kind des pos vel vff u_commit"   -> Step or Track, commit u_commit, print u
 *   "x kind des pos vel vff aff u_commit" -> StepEx with aff, commit u_commit, print u
 *   "e des pos vel vff aff"               -> PredictedError, print x[0] x[1] x[2] x[3]
 *   "w armed var_id val"                  -> XyCtrl_WriteParam, print return code (0 or -1)
 *   "r"                                   -> XyCtrl_ResetGainsToDefault + XyCtrl_Reset
 * Optional argv[1]: the predictor delay in ticks (XyCtrl_SetDelay). */
#include <stdio.h>
#include <stdlib.h>
#include "xy_ctrl.h"

int main(int argc, char **argv)
{
	XyCtrlState s;
	char op;
	if (argc > 1)
		XyCtrl_SetDelay(atoi(argv[1]));
	XyCtrl_Reset(&s);
	while (scanf(" %c", &op) == 1) {
		if (op == 's' || op == 't') {
			unsigned kind;
			float des, pos, vel, vff, uc, u = 0.0f;
			if (scanf("%u %f %f %f %f %f", &kind, &des, &pos, &vel, &vff, &uc) != 6)
				break;
			if (op == 's')
				u = XyCtrl_Step(&s, (uint8_t)kind, des, pos, vel, vff);
			else
				XyCtrl_Track(&s, (uint8_t)kind, pos);
			XyCtrl_Commit(&s, uc);
			printf("%.9g\n", u);
		} else if (op == 'x') {
			unsigned kind;
			float des, pos, vel, vff, aff, uc, u;
			if (scanf("%u %f %f %f %f %f %f", &kind, &des, &pos, &vel, &vff, &aff, &uc) != 7)
				break;
			u = XyCtrl_StepEx(&s, (uint8_t)kind, des, pos, vel, vff, aff, NULL);
			XyCtrl_Commit(&s, uc);
			printf("%.9g\n", u);
		} else if (op == 'e') {
			float des, pos, vel, vff, aff, x[4];
			if (scanf("%f %f %f %f %f", &des, &pos, &vel, &vff, &aff) != 5)
				break;
			XyCtrl_PredictedError(&s, des, pos, vel, vff, aff, NULL, x);
			printf("%.9g %.9g %.9g %.9g\n", x[0], x[1], x[2], x[3]);
		} else if (op == 'w') {
			unsigned armed, vid;
			float val;
			if (scanf("%u %u %f", &armed, &vid, &val) != 3)
				break;
			printf("%d\n", XyCtrl_WriteParam((uint8_t)armed, (uint8_t)vid, val));
		} else if (op == 'r') {
			XyCtrl_ResetGainsToDefault();
			XyCtrl_Reset(&s);
		}
	}
	return 0;
}
