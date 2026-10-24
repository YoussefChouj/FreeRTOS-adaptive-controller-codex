/* ============================================================================
 * API/tests/test_mrac_xy_ref_row.c
 *
 * HOST-TEST RUNNER ONLY - see sil_gate/README.md for the contract.
 *
 * D3, the x/y reference model per nominal kind (spec docs/design/2026-10-22-mrac-experiment-stack.md
 * item 4). Five tests:
 *
 *   1. boot               MRAC_Init leaves mrac_xy_ref_kind = 0 and x / y on row 0 (type 1, 2 rad/s,
 *                         zeta 0.8, no delay: the values MRAC_Init wrote before item 4).
 *   2. select             MRAC_XyRefSelect(k) puts row k on both x and y and sets the readback.
 *   3. overrides stay     a CMD 0x1D write (MRAC_VariantParamSet) after the select survives a repeat of
 *                         the same kind. Gate W (spec 2026-10-24 item 0): the write also survives another
 *                         kind, because an explicit write wins over the row default.
 *   4. bad kind           kind >= MRAC_XY_REF_N changes nothing.
 *   5. rows in bounds     every row is a value CMD 0x1D itself accepts (type, bw, delay) and zeta is in
 *                         [0.1, 2]; ticks <= MRAC_REF_BUF - 1.
 *   6. Gate W             an explicit write of type / bw / zeta / delay survives the latch (MRAC_XyRefSelect
 *                         on the ground), on both axes and in both orders (write then latch, latch then write).
 *   7. ref_zeta field     CMD 0x1D field MRAC_VF_REF_ZETA writes ref_model_zeta, inside its bounds.
 *
 * Fails to build on the code before item 4 (no MRAC_XyRefSelect / mrac_xy_ref_* symbols).
 * This file is NOT firmware. It is NEVER compiled into the drone's image.
 * ============================================================================
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

#include "mrac.h"

_imu_st imu_data = {0};
float MRAC_VectorNormSquare(const float *v, int n) { return 0.0f; }
float MRAC_Simple_RBF(float x, float c, float width) { (void)x; (void)c; (void)width; return 0.0f; }

static int fails, checks;
static void ok(const char *what, int cond)
{
    checks++;
    if (!cond) { fails++; printf("FAIL  %s\n", what); }
}

static int on_row(const MRAC_AxisConfig_t *c, const MRAC_XyRefRow_t *r)
{
    return c->ref_type == r->type && c->ref_model_bw == r->bw && c->ref_model_zeta == r->zeta
        && c->ref_delay_s == (float)r->delay_ticks * MRAC_DT;
}

int main(void)
{
    uint8_t k;
    MRAC_XyRefRow_t probe;

    /* 1. boot */
    MRAC_Init();
    ok("boot: kind 0", mrac_xy_ref_kind == 0U);
    ok("boot: x on row 0", on_row(&mrac_config_x, &mrac_xy_ref_rows[0]));
    ok("boot: y on row 0", on_row(&mrac_config_y, &mrac_xy_ref_rows[0]));
    ok("boot: the pre-item-4 x values", mrac_config_x.ref_type == 1.0f && mrac_config_x.ref_model_bw == 2.0f
        && mrac_config_x.ref_model_zeta == (float)0.8f && mrac_config_x.ref_delay_s == 0.0f);   /* cast: x87 */

    /* 2. select: a test row in place of the (all equal) table rows shows the copy, not a coincidence */
    for (k = 0U; k < MRAC_XY_REF_N; k++) {
        MRAC_Init();
        mrac_config_x.ref_model_bw = 7.0f; mrac_config_y.ref_model_bw = 7.0f;
        mrac_config_x.ref_delay_s = 0.5f;  mrac_config_y.ref_delay_s = 0.5f;
        if (k == 0U) MRAC_XyRefSelect(1U);
        MRAC_XyRefSelect(k);
        ok("select: readback", mrac_xy_ref_kind == k);
        ok("select: x on the row", on_row(&mrac_config_x, &mrac_xy_ref_rows[k]));
        ok("select: y on the row", on_row(&mrac_config_y, &mrac_xy_ref_rows[k]));
    }

    /* 3. a CMD 0x1D write after the select stays until another kind is selected */
    MRAC_Init();
    MRAC_XyRefSelect(2U);
    ok("override: 0x1D bw accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X, MRAC_VF_REF_MODEL_BW, 5.0f) == 1U);
    MRAC_XyRefSelect(2U);
    ok("override: same kind keeps the 0x1D write", mrac_config_x.ref_model_bw == 5.0f);
    MRAC_XyRefSelect(3U);
    /* Gate W (spec 2026-10-24 item 0): an explicit write wins over the row default, so the kind change does
     * NOT overwrite the written bandwidth. The fields that were not written still take the row. */
    ok("override: a new kind keeps the 0x1D bw (Gate W)", mrac_config_x.ref_model_bw == 5.0f);
    ok("override: a new kind still fills the untouched fields", mrac_config_x.ref_type == mrac_xy_ref_rows[3].type
        && mrac_config_x.ref_model_zeta == mrac_xy_ref_rows[3].zeta);
    ok("override: the readback follows the kind", mrac_xy_ref_kind == 3U);

    /* 4. bad kind */
    MRAC_Init();
    MRAC_XyRefSelect(1U);
    mrac_config_y.ref_model_bw = 9.0f;
    MRAC_XyRefSelect((uint8_t)MRAC_XY_REF_N);
    MRAC_XyRefSelect(0xFFU);
    ok("bad kind: readback unchanged", mrac_xy_ref_kind == 1U);
    ok("bad kind: y untouched", mrac_config_y.ref_model_bw == 9.0f);

    /* 5. every row is a value CMD 0x1D accepts */
    for (k = 0U; k < MRAC_XY_REF_N; k++) {
        probe = mrac_xy_ref_rows[k];
        MRAC_Init();
        ok("rows: type accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X, MRAC_VF_REF_TYPE, probe.type) == 1U);
        ok("rows: bw accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X, MRAC_VF_REF_MODEL_BW, probe.bw) == 1U);
        ok("rows: delay accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X, MRAC_VF_REF_DELAY_S,
                                                        (float)probe.delay_ticks * MRAC_DT) == 1U);
        ok("rows: ticks fit the ring", probe.delay_ticks <= (uint8_t)(MRAC_REF_BUF - 1));
        ok("rows: zeta in [0.1, 2]", probe.zeta >= 0.1f && probe.zeta <= 2.0f);
    }

    /* 6. Gate W: an explicit write wins over the row default, in both orders and on both axes.
     * The latch (MRAC_XyRefSelect on the ground) must not overwrite it. The literals are cast to float: on x87 a
     * double literal keeps its 80-bit value and the comparison against a stored float is false. */
    MRAC_Init();
    ok("gate W: bw write accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X, MRAC_VF_REF_MODEL_BW, 6.5f) == 1U);
    ok("gate W: zeta write accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X, MRAC_VF_REF_ZETA, 0.9f) == 1U);
    MRAC_XyRefSelect(1U);                       /* the latch, after the writes */
    ok("gate W: the written bw survives the latch", mrac_config_x.ref_model_bw == (float)6.5f);
    ok("gate W: the written zeta survives the latch", mrac_config_x.ref_model_zeta == (float)0.9f);
    ok("gate W: the other axis still takes the row (the write was on x only)",
       mrac_config_y.ref_model_bw == mrac_xy_ref_rows[1].bw
        && mrac_config_y.ref_model_zeta == mrac_xy_ref_rows[1].zeta);
    ok("gate W: the fields never written take the row", mrac_config_x.ref_type == mrac_xy_ref_rows[1].type
        && mrac_config_x.ref_delay_s == (float)mrac_xy_ref_rows[1].delay_ticks * MRAC_DT);

    MRAC_Init();
    MRAC_XyRefSelect(1U);                       /* the latch, before the writes */
    ok("gate W: a write after the latch is accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_Y,
                                                                            MRAC_VF_REF_MODEL_BW, 7.5f) == 1U);
    MRAC_XyRefSelect(2U);                       /* another latch, same ground window */
    ok("gate W: a write after the latch survives the next latch", mrac_config_y.ref_model_bw == (float)7.5f);
    ok("gate W: the other axis takes the row", mrac_config_x.ref_model_bw == mrac_xy_ref_rows[2].bw);

    /* Gate W: the record is per axis AND per field. A write of the bandwidth on x does not stop the row from
     * filling the damping on x, and a write on x does not stop the row from filling anything on y. */
    MRAC_Init();
    ok("gate W: per axis and field: bw on x accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X,
                                                                            MRAC_VF_REF_MODEL_BW, 8.5f) == 1U);
    MRAC_XyRefSelect(1U);
    ok("gate W: per axis and field: the x damping still takes the row",
       mrac_config_x.ref_model_zeta == mrac_xy_ref_rows[1].zeta);
    ok("gate W: per axis and field: the x type still takes the row",
       mrac_config_x.ref_type == mrac_xy_ref_rows[1].type);
    ok("gate W: per axis and field: y takes the whole row", mrac_config_y.ref_model_bw == mrac_xy_ref_rows[1].bw
        && mrac_config_y.ref_model_zeta == mrac_xy_ref_rows[1].zeta);

    /* Gate W: the record covers one ground window. When it ends (the disarm edge), the next flight starts from
     * the nominal row again, so a reference fitted for one flight never leaks into the next one. The edge is the
     * real one: MRAC_GateStep sees prev_armed = 1 and armed = 0. */
    MRAC_Init();
    ok("gate W: window: a write is accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_X,
                                                                     MRAC_VF_REF_MODEL_BW, 9.5f) == 1U);
    MRAC_XyRefSelect(1U);
    ok("gate W: window: the write survives inside the window", mrac_config_x.ref_model_bw == (float)9.5f);
    mrac_inj.prev_armed = 1;               /* armed last tick */
    mrac_in_armed = 0;                    /* disarmed now: the disarm edge */
    MRAC_GateStep();
    MRAC_XyRefSelect(2U);                 /* the next flight's latch */
    ok("gate W: window: the next flight starts from the row", mrac_config_x.ref_model_bw == mrac_xy_ref_rows[2].bw);

    /* a fresh boot drops the record: the row applies again */
    MRAC_Init();
    ok("gate W: a boot applies the row", on_row(&mrac_config_x, &mrac_xy_ref_rows[0]));

    /* 7. the ref_zeta field: written on any axis, refused outside its bounds.
     * The literals are cast to float: on x87 a double literal keeps its 80-bit value and the comparison against a
     * stored float is false (the same trap the boot check above notes). */
    MRAC_Init();
    ok("ref_zeta: accepted", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_YAW, MRAC_VF_REF_ZETA, 1.2f) == 1U);
    ok("ref_zeta: written", mrac_config_yaw.ref_model_zeta == (float)1.2f);
    ok("ref_zeta: below the bound refused", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_YAW, MRAC_VF_REF_ZETA,
                                                                 0.29f) == 0U);
    ok("ref_zeta: above the bound refused", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_YAW, MRAC_VF_REF_ZETA,
                                                                 1.51f) == 0U);
    ok("ref_zeta: NaN refused", MRAC_VariantParamSet((uint8_t)MRAC_AXIS_YAW, MRAC_VF_REF_ZETA,
                                                       (float)(0.0f / 0.0f)) == 0U);
    ok("ref_zeta: the value did not move", mrac_config_yaw.ref_model_zeta == (float)1.2f);

    printf("%s  %d/%d checks\n", fails ? "FAIL" : "PASS", checks - fails, checks);
    return fails ? 1 : 0;
}
