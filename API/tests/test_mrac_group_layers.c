/* API/tests/test_mrac_group_layers.c - host test of the all-loop MRAC layer selectors (spec 2026-10-24 item 1,
 * API/mrac.c MRAC_GroupLayersLatch, CMD 0x1D fields 33 att_layers / 34 yaw_layers / 35 z_layers).
 *
 * A. Boot: the three targets and live values = INNER (today's behaviour).
 * B. Range: 0..3 accepted on any axis (0 and 4); 4, -1 and NaN refused, and a refusal leaves the target alone.
 * C. Ground latch: a write changes the target only; live follows at MRAC_GroupLayersLatch, which keeps only the
 *    INNER / OUTER bits (a target poked to 0xFF latches as BOTH). The two latches do not touch each other.
 * D. Wire: CMD 0x1D idx 0xC2 / 0xC4 / 0xC6 (axis x encoding) reach fields 33 / 34 / 35; 0xC3 (axis y) too.
 * E. Independence: each field writes only its own target; field 31 (xy) is untouched.
 * F. Rounding: 2.4 -> 2, 2.6 -> 3 (as field 31).
 *
 * Built by tools/host_tests.py, row mrac_group_layers (-DMRAC_VARIANT=2), same build as row mrac_pos_layer:
 *   gcc -std=c99 -Wall -Wextra -DMRAC_VARIANT=2 -I<copy> -IAPI/tests/stubs API/tests/test_mrac_group_layers.c
 *       <copy>/mrac.c <copy>/mrac_math.c -lm
 */
#include <math.h>
#include <stdio.h>

#include "mrac.h"

_imu_st imu_data;

static int checks, fails;
static void ok(const char *what, int cond)
{
    checks++;
    if (!cond) {
        fails++;
        printf("FAIL %s\n", what);
    }
}

static int set(uint8_t axis, uint8_t field, float val)
{
    return MRAC_VariantParamSet(axis, field, val) == 1U;
}

int main(void)
{
    const uint8_t inner = (uint8_t)MRAC_LAYER_INNER;
    const uint8_t outer = (uint8_t)MRAC_LAYER_OUTER;
    const uint8_t both = (uint8_t)MRAC_LAYER_BOTH;

    /* A. Boot */
    MRAC_Init();
    ok("A att target INNER", mrac_att_layers == inner);
    ok("A yaw target INNER", mrac_yaw_layers == inner);
    ok("A z target INNER", mrac_z_layers == inner);
    ok("A att live INNER", mrac_att_layers_live == inner);
    ok("A yaw live INNER", mrac_yaw_layers_live == inner);
    ok("A z live INNER", mrac_z_layers_live == inner);
    ok("A field numbers 33/34/35", MRAC_VF_ATT_LAYERS == 33 && MRAC_VF_YAW_LAYERS == 34 && MRAC_VF_Z_LAYERS == 35);
    ok("A BOTH = INNER | OUTER", both == (uint8_t)(inner | outer) && inner != outer);

    /* B. Range, any axis */
    ok("B att 0 on axis 0", set(0, MRAC_VF_ATT_LAYERS, 0.0f));
    ok("B att 3 on axis x", set(MRAC_AXIS_X, MRAC_VF_ATT_LAYERS, 3.0f));
    ok("B att target 3", mrac_att_layers == both);
    ok("B att 4 refused", !set(0, MRAC_VF_ATT_LAYERS, 4.0f));
    ok("B att -1 refused", !set(0, MRAC_VF_ATT_LAYERS, -1.0f));
    ok("B att NaN refused", !set(0, MRAC_VF_ATT_LAYERS, NAN));
    ok("B att target kept after refusals", mrac_att_layers == both);
    ok("B yaw 4 refused", !set(MRAC_AXIS_Y, MRAC_VF_YAW_LAYERS, 4.0f));
    ok("B z -1 refused", !set(2, MRAC_VF_Z_LAYERS, -1.0f));

    /* C. Ground latch: target only until the latch */
    MRAC_Init();
    ok("C att 3 accepted", set(0, MRAC_VF_ATT_LAYERS, 3.0f));
    ok("C yaw 2 accepted", set(2, MRAC_VF_YAW_LAYERS, 2.0f));
    ok("C z 0 accepted", set(3, MRAC_VF_Z_LAYERS, 0.0f));
    ok("C att live unchanged before latch", mrac_att_layers_live == inner);
    ok("C yaw live unchanged before latch", mrac_yaw_layers_live == inner);
    ok("C z live unchanged before latch", mrac_z_layers_live == inner);
    MRAC_XyLayersLatch();
    ok("C xy latch leaves group live alone",
       mrac_att_layers_live == inner && mrac_yaw_layers_live == inner && mrac_z_layers_live == inner);
    MRAC_GroupLayersLatch();
    ok("C att live BOTH", mrac_att_layers_live == both);
    ok("C yaw live OUTER", mrac_yaw_layers_live == outer);
    ok("C z live off", mrac_z_layers_live == 0U);
    mrac_att_layers = 0xFFU;
    MRAC_GroupLayersLatch();
    ok("C 0xFF masks to BOTH", mrac_att_layers_live == both);
    {
        uint8_t xy_live = mrac_xy_layers_live;
        mrac_xy_layers = 0U;
        MRAC_GroupLayersLatch();
        ok("C group latch leaves xy live alone", mrac_xy_layers_live == xy_live);
    }

    /* D. Wire index, axis x / y encoding: 0x80 | (field << 1) | (axis - 4) */
    MRAC_Init();
    ok("D idx 0xC2 = field 33 x", MRAC_VariantIdxSet(0xC2U, 0.0f) == 1U && mrac_att_layers == 0U);
    ok("D idx 0xC4 = field 34 x", MRAC_VariantIdxSet(0xC4U, 2.0f) == 1U && mrac_yaw_layers == outer);
    ok("D idx 0xC6 = field 35 x", MRAC_VariantIdxSet(0xC6U, 3.0f) == 1U && mrac_z_layers == both);
    ok("D idx 0xC3 = field 33 y", MRAC_VariantIdxSet(0xC3U, 1.0f) == 1U && mrac_att_layers == inner);
    ok("D idx 0xC6 value 4 refused", MRAC_VariantIdxSet(0xC6U, 4.0f) == 0U && mrac_z_layers == both);

    /* E. Independence */
    MRAC_Init();
    {
        uint8_t xy = mrac_xy_layers;
        ok("E att 0", set(0, MRAC_VF_ATT_LAYERS, 0.0f));
        ok("E only att moved", mrac_att_layers == 0U && mrac_yaw_layers == inner && mrac_z_layers == inner);
        ok("E yaw 3", set(0, MRAC_VF_YAW_LAYERS, 3.0f));
        ok("E only yaw moved", mrac_att_layers == 0U && mrac_yaw_layers == both && mrac_z_layers == inner);
        ok("E z 2", set(0, MRAC_VF_Z_LAYERS, 2.0f));
        ok("E only z moved", mrac_att_layers == 0U && mrac_yaw_layers == both && mrac_z_layers == outer);
        ok("E xy target untouched", mrac_xy_layers == xy);
    }

    /* F. Rounding */
    ok("F 2.4 accepted", set(0, MRAC_VF_ATT_LAYERS, 2.4f));
    ok("F 2.4 -> 2", mrac_att_layers == 2U);
    ok("F 2.6 accepted", set(0, MRAC_VF_ATT_LAYERS, 2.6f));
    ok("F 2.6 -> 3", mrac_att_layers == 3U);

    printf("mrac_group_layers: %d checks, %d fails\n", checks, fails);
    return fails ? 1 : 0;
}
