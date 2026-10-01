/*
 * gcc -std=c99 -Wall -Wextra -DSTUBS_ROBOT_TYPES_H -IAPI/tests/stubs -IAPI API/tests/test_drift_fix.c API/pid.c -lm -o C:/tmp/wp13/tdf.exe && C:/tmp/wp13/tdf.exe
 */
#include <stdio.h>
#include <math.h>
#include "pid.h"

float Cos_Yaw = 1.0f;
float Sin_Yaw = 0.0f;

int test_compute_pid_gated(void) {
    PIDTypeDef pPID;
    
    // a. ground: pitchPID row, Des 5 FB 0, 1000 x ComputePID_Gated(...,0)
    pPID.Kp = 3.0f;
    pPID.Ki = 0.02f;
    pPID.Kd = 8.0f;
    pPID.UMax = 200.0f;
    pPID.UpMax = 200.0f;
    pPID.UiMax = 26.0f;
    pPID.UdMax = 10.0f;
    pPID.SumEMax = 1300.0f;
    pPID.EMin = 10.0f;
    
    pPID.Des = 5.0f;
    pPID.FB = 0.0f;
    pPID.SumE = 0.0f;
    pPID.Ui = 0.0f;
    pPID.Up = 0.0f;
    pPID.Ud = 0.0f;
    pPID.PreE = 0.0f;
    pPID.U = 0.0f;
    pPID.aw_mode = AW_LEGACY;

    for (int i = 0; i < 1000; i++) {
        ComputePID_Gated(&pPID, 0);
    }
    float expected_U = pPID.Up + pPID.Ud;
    if (pPID.SumE != 0.0f || pPID.Ui != 0.0f || pPID.U != expected_U) {
        printf("FAIL: ground test. SumE=%f, Ui=%f, U=%f, Expected U=%f\n", pPID.SumE, pPID.Ui, pPID.U, expected_U);
        return 1;
    }
    printf("PASS: ground test\n");

    // b. flying
    pPID.Des = 5.0f;
    pPID.FB = 0.0f;
    pPID.SumE = 0.0f;
    pPID.Ui = 0.0f;
    pPID.PreE = 0.0f;
    
    for (int i = 0; i < 1000; i++) {
        ComputePID_Gated(&pPID, 1);
    }
    
    if (fabsf(pPID.SumE - 1300.0f) > 0.1f || fabsf(pPID.Ui - 26.0f) > 0.1f) {
        printf("FAIL: flying test pitchPID. SumE=%f, Ui=%f\n", pPID.SumE, pPID.Ui);
        return 1;
    }
    printf("PASS: flying test pitchPID\n");

    // gyroxPID
    pPID.Kp = 5.0f;
    pPID.Ki = 0.01f;
    pPID.Kd = 10.0f;
    pPID.UMax = 300.0f;
    pPID.UpMax = 300.0f;
    pPID.UiMax = 160.0f;
    pPID.UdMax = 100.0f;
    pPID.SumEMax = 16000.0f;
    pPID.EMin = 50.0f;
    pPID.Des = 25.0f; // E = 25 - 0 = 25 < 50
    pPID.FB = 0.0f;
    pPID.SumE = 0.0f;
    pPID.Ui = 0.0f;
    pPID.PreE = 0.0f;

    for (int i = 0; i < 2000; i++) {
        ComputePID_Gated(&pPID, 1);
    }

    if (fabsf(pPID.SumE - 16000.0f) > 0.1f || fabsf(pPID.Ui - 160.0f) > 0.1f) {
        printf("FAIL: flying test gyroxPID. SumE=%f, Ui=%f\n", pPID.SumE, pPID.Ui);
        return 1;
    }
    printf("PASS: flying test gyroxPID\n");

    // locxsPID
    pPID.Kp = 3.0f;
    pPID.Ki = 0.008f;
    pPID.Kd = 6.0f;
    pPID.UMax = 600.0f;
    pPID.UpMax = 600.0f;
    pPID.UiMax = 100.0f;
    pPID.UdMax = 100.0f;
    pPID.SumEMax = 12500.0f;
    pPID.EMin = 10.0f;
    pPID.Des = 5.0f; // E = 5 < 10
    pPID.FB = 0.0f;
    pPID.SumE = 0.0f;
    pPID.Ui = 0.0f;
    pPID.PreE = 0.0f;

    for (int i = 0; i < 3000; i++) {
        ComputePID_Gated(&pPID, 1);
    }
    if (fabsf(pPID.SumE - 12500.0f) > 0.1f || fabsf(pPID.Ui - 100.0f) > 0.1f) {
        printf("FAIL: flying test locxsPID. SumE=%f, Ui=%f\n", pPID.SumE, pPID.Ui);
        return 1;
    }
    printf("PASS: flying test locxsPID\n");

    return 0;
}

int test_att_trim(void) {
    float res = AttTrim_Apply(10.0f, -2.0f, 30.0f, 0);
    if (res != 10.0f) {
        printf("FAIL: AttTrim not flying\n"); return 1;
    }
    res = AttTrim_Apply(10.0f, -2.0f, 30.0f, 1);
    if (res != 8.0f) {
        printf("FAIL: AttTrim flying\n"); return 1;
    }
    res = AttTrim_Apply(29.0f, 2.0f, 30.0f, 1);
    if (res != 30.0f) {
        printf("FAIL: AttTrim clamped limit. Expected 30.0, got %f\n", res); return 1;
    }
    res = AttTrim_Apply(-29.0f, -2.0f, 30.0f, 1);
    if (res != -30.0f) {
        printf("FAIL: AttTrim clamped -limit. Expected -30.0, got %f\n", res); return 1;
    }
    printf("PASS: AttTrim_Apply\n");
    return 0;
}

int test_traj_ff(void) {
    TrajFF_t s;
    TrajFF_Reset(&s);
    float vx, vy, ax, ay;

    for (int i = 0; i < 500; i++) {
        TrajFF_Step(&s, 0, 10.0f, 20.0f, 0.01f, 0.2f, 100.0f, &vx, &vy, &ax, &ay);
        if (vx != 0.0f || vy != 0.0f || ax != 0.0f || ay != 0.0f) {
            printf("FAIL: TrajFF inactive\n"); return 1;
        }
    }
    printf("PASS: TrajFF inactive\n");

    TrajFF_Reset(&s);
    float max_ax = 0.0f;
    for (int i = 0; i < 100; i++) {
        float t = i * 0.01f;
        TrajFF_Step(&s, 1, 30.0f * t, -20.0f * t, 0.01f, 0.2f, 100.0f, &vx, &vy, &ax, &ay);
        if (fabsf(ax) > max_ax) max_ax = fabsf(ax);
    }
    if (fabsf(vx - 30.0f) > 1e-3f || fabsf(vy + 20.0f) > 1e-3f || max_ax == 0.0f || fabsf(ax) > max_ax * 0.5f) {
        printf("FAIL: TrajFF ramp. vx=%f, vy=%f, ax=%f, max_ax=%f\n", vx, vy, ax, max_ax); return 1;
    }
    printf("PASS: TrajFF ramp\n");

    TrajFF_Step(&s, 0, 30.0f, -20.0f, 0.01f, 0.2f, 100.0f, &vx, &vy, &ax, &ay);
    if (vx != 0.0f || vy != 0.0f || ax != 0.0f || ay != 0.0f) {
        printf("FAIL: TrajFF going inactive\n"); return 1;
    }
    printf("PASS: TrajFF going inactive\n");

    return 0;
}

int main(void) {
    int ret = 0;
    ret |= test_compute_pid_gated();
    ret |= test_att_trim();
    ret |= test_traj_ff();
    return ret;
}
