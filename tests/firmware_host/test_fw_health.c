/* WP-40 firmware health helpers: IWDG reload, reset-cause decode, alive check, stack minimum, CPU share. */
#include <stdio.h>
#include <stdint.h>
#include "fw_health.h"

static int s_check_count = 0;
static int s_fail_count = 0;

#define CHECK(cond) do { \
    s_check_count++; \
    if (!(cond)) { \
        s_fail_count++; \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

static void test_table_defaults(void)
{
    CHECK(FwHealth_IwdgEnabled() == 0U);                        /* shipped disabled */
    CHECK(FwHealth_IwdgTimeoutMs() == 2500U);
}

static void test_iwdg_reload(void)
{
    CHECK(FwHealth_IwdgReload(2500U) == 1250U);
    CHECK(FwHealth_IwdgReload(0U) == 1U);
    CHECK(FwHealth_IwdgReload(8190U) == 4095U);
    CHECK(FwHealth_IwdgReload(100000U) == 4095U);
    /* worst case LSI 47 kHz still outlasts the 1 s monitor period */
    CHECK((float)FwHealth_IwdgReload(FwHealth_IwdgTimeoutMs()) * 64.0f / 47000.0f > 1.5f);
}

static void test_reset_cause(void)
{
    CHECK(FwHealth_ResetCause(0x0C000000U) == (FW_RESET_PIN | FW_RESET_POR));   /* PINRSTF | PORRSTF */
    CHECK(FwHealth_ResetCause(0x24000000U) == (FW_RESET_PIN | FW_RESET_IWDG));  /* IWDGRSTF | PINRSTF */
    CHECK(FwHealth_ResetCause(0x10000000U) == FW_RESET_SFT);
    CHECK(FwHealth_ResetCause(0x80000000U) == FW_RESET_LPWR);
    CHECK(FwHealth_ResetCause(0x02000000U) == FW_RESET_BOR);
    CHECK(FwHealth_ResetCause(0x01FFFFFFU) == 0U);                              /* low bits ignored */
}

static void test_alive(void)
{
    uint16_t fps[4] = { 1000U, 1000U, 200U, 70U };
    CHECK(FwHealth_Alive(fps, 4U) == 1U);
    fps[2] = 0U;
    CHECK(FwHealth_Alive(fps, 4U) == 0U);
    CHECK(FwHealth_Alive(fps, 0U) == 0U);
    CHECK(FwHealth_Alive(NULL, 4U) == 0U);
}

static void test_min_index(void)
{
    uint16_t v[5] = { 300U, 120U, 500U, 120U, 900U };
    CHECK(FwHealth_MinIndex(v, 5U) == 1U);                      /* first of the ties */
    CHECK(FwHealth_MinIndex(v, 1U) == 0U);
    CHECK(FwHealth_MinIndex(v, 0U) == FW_HEALTH_NONE);
    CHECK(FwHealth_MinIndex(NULL, 5U) == FW_HEALTH_NONE);
}

static void test_pct(void)
{
    CHECK(FwHealth_Pct(0U, 0U) == 0.0f);
    CHECK(FwHealth_Pct(42U, 168U) == 25.0f);
    /* CYCCNT deltas taken with unsigned wrap stay correct across the 25.6 s rollover */
    {
        uint32_t t0 = 0xFFFFFF00U, t1 = 0x00000100U;            /* total advanced by 0x200 */
        uint32_t r0 = 0xFFFFFFC0U, r1 = 0x00000040U;            /* task advanced by 0x80 */
        CHECK(FwHealth_Pct(r1 - r0, t1 - t0) == 25.0f);
    }
}

int main(void)
{
    CHECK(g_rtos_budget.stack_min_task == FW_HEALTH_NONE && g_rtos_budget.iwdg_on == 0U);
    test_table_defaults();
    test_iwdg_reload();
    test_reset_cause();
    test_alive();
    test_min_index();
    test_pct();
    printf("test_fw_health: %d checks, %d failed\n", s_check_count, s_fail_count);
    return s_fail_count == 0 ? 0 : 1;
}
