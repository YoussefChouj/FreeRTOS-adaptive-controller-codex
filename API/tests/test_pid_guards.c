/* Host test of the PID NaN guards (WP-36).
 *
 * ComputeYawPID gained an input guard (after E is formed) and an output guard; ComputePID's zeroing moved
 * into the shared helpers. Finite inputs must give the bit-identical state of the wp/33 code (the
 * reference copies below, comments dropped). A non-finite input or state must give U = 0 and a clean
 * integrator, and the next finite tick must continue from that clean state.
 *
 *   gcc -std=c99 -Wall -Wextra -Wno-missing-field-initializers -DSTUBS_ROBOT_TYPES_H -IAPI/tests/stubs -IAPI \
 *       API/tests/test_pid_guards.c API/pid.c -lm -o tpg && ./tpg      (or: python tools/host_tests.py pid_guards)
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include "pid.h"

float Cos_Yaw = 1.0f;
float Sin_Yaw = 0.0f;

static int g_checks, g_fail;

static void check(int ok, const char *what, long step)
{
    g_checks++;
    if (!ok) {
        g_fail++;
        if (g_fail <= 10) printf("FAIL %s (step %ld)\n", what, step);
    }
}

/* ---- reference: API/pid.c at wp/33 ------------------------------------------------------------- */
#define REF_NONFINITE(x) (((x) != (x)) || ((x) > 1e12f) || ((x) < -1e12f))

static void ref_zero(PIDTypeDef *pPID)
{
    pPID->SumE = 0.0f; pPID->PreE = 0.0f; pPID->E = 0.0f;
    pPID->Up = 0.0f; pPID->Ui = 0.0f; pPID->Ud = 0.0f; pPID->U = 0.0f;
}

static void ref_pid(PIDTypeDef *pPID)
{
    if (REF_NONFINITE(pPID->Des) || REF_NONFINITE(pPID->FB) ||
        REF_NONFINITE(pPID->SumE) || REF_NONFINITE(pPID->PreE)) { ref_zero(pPID); return; }
    pPID->E = pPID->Des - pPID->FB;
    if (REF_NONFINITE(pPID->E)) { ref_zero(pPID); return; }
    if (pPID->aw_mode == AW_CLAMP) {
        float SumE_prev = pPID->SumE;
        float u_presat;
        pPID->SumE += pPID->E;
        value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
        pPID->Ui = pPID->Ki * pPID->SumE;
        value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
        pPID->Up = pPID->Kp * pPID->E;
        value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
        pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
        value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );
        u_presat = pPID->Up + pPID->Ui + pPID->Ud;
        pPID->U = u_presat;
        value_limit( pPID->U , -pPID->UMax , pPID->UMax );
        if( pPID->U != u_presat &&
            ((u_presat > 0 && pPID->E > 0) || (u_presat < 0 && pPID->E < 0)) ) {
            pPID->SumE = SumE_prev;
            pPID->Ui = pPID->Ki * pPID->SumE;
            value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
            pPID->U = pPID->Up + pPID->Ui + pPID->Ud;
            value_limit( pPID->U , -pPID->UMax , pPID->UMax );
        }
    } else if (pPID->aw_mode == AW_BACKCALC) {
        float u_presat;
        pPID->SumE += pPID->E;
        value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
        pPID->Ui = pPID->Ki * pPID->SumE;
        value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
        pPID->Up = pPID->Kp * pPID->E;
        value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
        pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
        value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );
        u_presat = pPID->Up + pPID->Ui + pPID->Ud;
        pPID->U = u_presat;
        value_limit( pPID->U , -pPID->UMax , pPID->UMax );
        pPID->SumE += pPID->Kt * (pPID->U - u_presat);
        value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
    } else {
        if(((pPID->U <= pPID->UMax && pPID->E > 0) || (pPID->U >= -pPID->UMax && pPID->E < 0))
            && ABS(pPID->E) < pPID->EMin) {
            pPID->SumE += pPID->E;
        }
        value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
        pPID->Ui = pPID->Ki * pPID->SumE;
        value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
        pPID->Up = pPID->Kp * pPID->E;
        value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
        pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
        value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );
        pPID->U = pPID->Up + pPID->Ui + pPID->Ud;
        value_limit( pPID->U , -pPID->UMax , pPID->UMax );
    }
    if (REF_NONFINITE(pPID->U) || REF_NONFINITE(pPID->SumE)) { pPID->SumE = 0.0f; pPID->U = 0.0f; }
    pPID->PreE = pPID->E ;
}

static void ref_yaw(PIDTypeDef *pPID)
{
    pPID->E = fmodf(pPID->Des - pPID->FB, 360.0f);
    if(pPID->E>=180)pPID->E-=360;
    if(pPID->E<=-180)pPID->E+=360;
    if(((pPID->U <= pPID->UMax && pPID->E > 0) || (pPID->U >= -pPID->UMax && pPID->E < 0))
        && ABS(pPID->E) < pPID->EMin) {
        pPID->SumE += pPID->E;
    }
    value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
    pPID->Ui = pPID->Ki * pPID->SumE;
    value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
    pPID->Up = pPID->Kp * pPID->E;
    value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
    pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
    value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );
    pPID->U = pPID->Up + pPID->Ui + pPID->Ud;
    value_limit( pPID->U , -pPID->UMax , pPID->UMax );
    pPID->PreE = pPID->E ;
}

/* ---- helpers ------------------------------------------------------------------------------------ */
static unsigned g_lcg = 12345u;

static float rnd(float lo, float hi)
{
    g_lcg = g_lcg * 1664525u + 1013904223u;
    return lo + (hi - lo) * (float)(g_lcg >> 8) / 16777216.0f;
}

static uint32_t bits(float f)
{
    uint32_t u;
    memcpy(&u, &f, sizeof u);
    return u;
}

/* Bit-exact, NaN payloads and the sign of zero included. */
static int same(const PIDTypeDef *a, const PIDTypeDef *b)
{
    const float fa[] = { a->FB, a->Des, a->Kp, a->Ki, a->Kd, a->Up, a->Ui, a->Ud, a->E, a->PreE,
                         a->SumE, a->U, a->UMax, a->UpMax, a->UiMax, a->UdMax, a->SumEMax, a->EMin, a->Kt };
    const float fb[] = { b->FB, b->Des, b->Kp, b->Ki, b->Kd, b->Up, b->Ui, b->Ud, b->E, b->PreE,
                         b->SumE, b->U, b->UMax, b->UpMax, b->UiMax, b->UdMax, b->SumEMax, b->EMin, b->Kt };
    unsigned i;
    for (i = 0; i < sizeof fa / sizeof fa[0]; i++) {
        if (bits(fa[i]) != bits(fb[i])) return 0;
    }
    return a->aw_mode == b->aw_mode;
}

static int zeroed(const PIDTypeDef *p)
{
    return p->U == 0.0f && p->SumE == 0.0f && p->E == 0.0f && p->PreE == 0.0f &&
           p->Up == 0.0f && p->Ui == 0.0f && p->Ud == 0.0f;
}

static const float BAD[] = { NAN, INFINITY, -INFINITY };

/* ---- tests -------------------------------------------------------------------------------------- */

/* ComputePID after the helper refactor == wp/33 ComputePID, every anti-windup mode, with non-finite
 * Des / FB / SumE injected now and then (the guard paths must match too). */
static void test_compute_pid_equiv(void)
{
    const PIDTypeDef rows[2] = { Ctrler.gyroxPID, Ctrler.pitchPID };
    int r, aw;
    long k;
    for (r = 0; r < 2; r++) {
        for (aw = AW_LEGACY; aw <= AW_BACKCALC; aw++) {
            PIDTypeDef a = rows[r], b;
            a.aw_mode = aw;
            a.Kt = 0.5f;
            b = a;
            for (k = 0; k < 50000; k++) {
                float des = rnd(-400.0f, 400.0f), fb = rnd(-400.0f, 400.0f);
                if (k % 97 == 0) des = BAD[k % 3];
                if (k % 211 == 0) fb = BAD[k % 3];
                a.Des = b.Des = des;
                a.FB = b.FB = fb;
                if (k % 503 == 0) a.SumE = b.SumE = BAD[k % 3];
                ComputePID(&a);
                ref_pid(&b);
                check(same(&a, &b), "ComputePID == wp/33 reference", k);
            }
        }
    }
}

/* ComputeYawPID == wp/33 for finite inputs, including Des / FB several turns apart and the 2e12 case
 * (E is folded first, so a large finite heading does not trip the guard). */
static void test_yaw_equiv_finite(void)
{
    PIDTypeDef rows[2];
    int r;
    long k;
    rows[0] = Ctrler.yawPID;
    rows[1] = Ctrler.yawPID;
    rows[1].Ki = 0.04f; rows[1].Kd = 1.5f; rows[1].EMin = 1000.0f;   /* integrate on every tick */
    for (r = 0; r < 2; r++) {
        PIDTypeDef a = rows[r], b = rows[r];
        for (k = 0; k < 100000; k++) {
            a.Des = b.Des = rnd(-1000.0f, 1000.0f);
            a.FB = b.FB = (k % 1009 == 0) ? 2e12f : rnd(-1000.0f, 1000.0f);
            ComputeYawPID(&a);
            ref_yaw(&b);
            check(same(&a, &b), "ComputeYawPID == wp/33 reference (finite)", k);
        }
    }
}

/* Non-finite Des, FB, SumE or PreE: the loop is zeroed (wp/33 put NaN or Inf into U), then recovers. */
static void test_yaw_guard(void)
{
    int i, field;
    for (field = 0; field < 4; field++) {
        for (i = 0; i < 3; i++) {
            PIDTypeDef a = Ctrler.yawPID, b;
            a.EMin = 1000.0f;
            a.Des = 30.0f; a.FB = 10.0f; a.SumE = 20.0f; a.PreE = 3.0f; a.U = 50.0f;
            b = a;
            switch (field) {
            case 0: a.Des = b.Des = BAD[i]; break;
            case 1: a.FB = b.FB = BAD[i]; break;
            case 2: a.SumE = b.SumE = BAD[i]; break;
            default: a.PreE = b.PreE = BAD[i]; break;
            }
            ComputeYawPID(&a);
            ref_yaw(&b);
            check(zeroed(&a), "non-finite input zeroes the yaw loop", field * 3 + i);
            /* wp/33 put it into U, except PreE and an infinite SumE (value_limit clamps those to SumEMax) */
            check(field == 3 || (field == 2 && i > 0) || !isfinite(b.U), "wp/33 let it through to U (guard is exercised)", field * 3 + i);

            /* next tick, finite again: continues from the clean state */
            a.Des = 25.0f; a.FB = 12.0f;
            b = a;
            ComputeYawPID(&a);
            ref_yaw(&b);
            check(same(&a, &b) && isfinite(a.U) && a.U != 0.0f, "yaw loop recovers on the next finite tick", field * 3 + i);
        }
    }
    {
        PIDTypeDef a = Ctrler.yawPID;   /* a finite but absurd integrator counts as poisoned, as in ComputePID */
        a.Des = 5.0f; a.FB = 0.0f; a.SumE = 2e12f;
        ComputeYawPID(&a);
        check(zeroed(&a), "|SumE| > 1e12 zeroes the yaw loop", 0);
    }
}

int main(void)
{
    test_compute_pid_equiv();
    test_yaw_equiv_finite();
    test_yaw_guard();
    printf("%d checks, %d failure(s)\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
