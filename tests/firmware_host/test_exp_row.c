#include <stdio.h>
#include <stdint.h>
#include <assert.h>
#include "exp_row.h"

#define EXP_ROW(name, xy, kp, vp, lim, mdec, xyrm, layer)  { (xy), (kp), (vp), (lim), (mdec), (xyrm), (layer) }

static const ExpRow_t s_exp[] = {
    /*         name       xy    kp    vp    lim   mdec  xyrm  layer */
    EXP_ROW(   pid,       0U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 0 */
    EXP_ROW(   adrc,      1U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 1 */
    EXP_ROW(   lqr,       2U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 2 */
    EXP_ROW(   mpc,       3U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 3 */
    EXP_ROW(   pid_3l,    0U,   7U,   16U,  0U,   1U,   -1,   0U ),  /* 4 */
    EXP_ROW(   adrc_3l,   1U,   7U,   16U,  0U,   1U,   -1,   0U ),  /* 5 */
    EXP_ROW(   lqr_3l,    2U,   7U,   16U,  0U,   1U,   -1,   0U ),  /* 6 */
    EXP_ROW(   mpc_3l,    3U,   7U,   16U,  0U,   1U,   -1,   0U ),  /* 7 */
};

int main(void)
{
    volatile uint8_t xy_kind = 0U;
    volatile uint8_t kp_id = 7U;
    volatile uint8_t vp_id = 0U;
    volatile uint8_t lim_mode = 0U;
    volatile uint8_t mdec = 1U;
    ExpKnobs_t k = { &xy_kind, &kp_id, &vp_id, &lim_mode, &mdec };
    uint8_t last = 0xFFU;
    uint8_t active = 0xFFU;
    int8_t xyrm = -1;
    uint8_t res;
    int i;

    /* 1. exp_id never written (0xFF): knobs untouched, returns 0xFF */
    res = Exp_Step(0xFFU, &last, active, 1U, s_exp, 8U, &k, &xyrm);
    assert(res == 0xFFU);
    assert(last == 0xFFU);
    assert(xy_kind == 0U && kp_id == 7U && vp_id == 0U && lim_mode == 0U && mdec == 1U);

    /* 2. Each of the 8 rows writes exactly its columns */
    for (i = 0; i < 8; i++) {
        xy_kind = 99U;
        kp_id = 99U;
        vp_id = 99U;
        lim_mode = 99U;
        mdec = 99U;
        last = 0xFFU;
        active = 0xFFU;
        xyrm = -99;

        res = Exp_Step((uint8_t)i, &last, active, 1U, s_exp, 8U, &k, &xyrm);
        assert(res == (uint8_t)i);
        assert(last == (uint8_t)i);
        assert(xy_kind == s_exp[i].xy);
        assert(kp_id == s_exp[i].kp);
        assert(vp_id == s_exp[i].vp);
        assert(lim_mode == s_exp[i].lim);
        assert(mdec == s_exp[i].mdec);
        assert(xyrm == (int8_t)s_exp[i].xy);
    }

    /* 3. 0xFF / -1 keep columns untouched */
    {
        ExpRow_t custom[1] = { { 0xFFU, 0xFFU, 12U, 0xFFU, 0xFFU, -1, 0U } };
        xy_kind = 2U;
        kp_id = 5U;
        vp_id = 0U;
        lim_mode = 1U;
        mdec = 0U;
        last = 0xFFU;
        active = 0U;
        xyrm = -99;

        res = Exp_Step(0U, &last, active, 1U, custom, 1U, &k, &xyrm);
        assert(res == 0U);
        assert(last == 0U);
        assert(xy_kind == 2U);  /* untouched */
        assert(kp_id == 5U);    /* untouched */
        assert(vp_id == 12U);   /* written */
        assert(lim_mode == 1U); /* untouched */
        assert(mdec == 0U);     /* untouched */
        assert(xyrm == 2);      /* auto from xy_kind */

        /* Explicit xyrm */
        custom[0].xyrm = 3;
        last = 0xFFU;
        res = Exp_Step(0U, &last, active, 1U, custom, 1U, &k, &xyrm);
        assert(xyrm == 3);
    }

    /* 4. In-air request deferred, then applied on the ground */
    {
        last = 0U;
        active = 0U;
        xy_kind = 0U;
        kp_id = 7U;
        vp_id = 0U;
        lim_mode = 0U;
        mdec = 1U;
        xyrm = 0;

        /* In air: on_ground = 0 */
        res = Exp_Step(4U, &last, active, 0U, s_exp, 8U, &k, &xyrm);
        assert(res == 0U);       /* returns active */
        assert(last == 0U);      /* last unchanged */
        assert(vp_id == 0U);     /* not written */

        /* On ground: on_ground = 1 */
        res = Exp_Step(4U, &last, active, 1U, s_exp, 8U, &k, &xyrm);
        assert(res == 4U);       /* applied */
        assert(last == 4U);      /* last updated */
        assert(vp_id == 16U);    /* written */
    }

    /* 5. Bad ID -> 0xEE and nothing written */
    {
        last = 4U;
        active = 4U;
        xy_kind = 0U;
        kp_id = 7U;
        vp_id = 16U;
        lim_mode = 0U;
        mdec = 1U;

        res = Exp_Step(8U, &last, active, 1U, s_exp, 8U, &k, &xyrm);
        assert(res == 0xEEU);
        assert(last == 8U);
        assert(xy_kind == 0U && kp_id == 7U && vp_id == 16U && lim_mode == 0U && mdec == 1U);

        res = Exp_Step(250U, &last, 0xEEU, 1U, s_exp, 8U, &k, &xyrm);
        assert(res == 0xEEU);
        assert(last == 250U);
        assert(xy_kind == 0U && kp_id == 7U && vp_id == 16U && lim_mode == 0U && mdec == 1U);
    }

    printf("fw_exp_row: all checks passed\n");
    return 0;
}
