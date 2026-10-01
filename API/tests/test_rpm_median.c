/*
 * Host test for rpm_median5 (BSP/rpm_median.h, header-inline).
 *
 * Build+run command:
 *   gcc -std=c99 -Wall -Wextra -IBSP API/tests/test_rpm_median.c -o /tmp/wp15/trm && /tmp/wp15/trm
 */
#include <stdio.h>
#include <stdint.h>
#include <assert.h>

#include "rpm_median.h"

#define ABS(x) ((x) < 0 ? -(x) : (x))

static int fail_count = 0;
static int pass_count = 0;

static void check(const char* name, int cond)
{
    if (cond) {
        pass_count++;
    } else {
        fail_count++;
        printf("FAIL: %s\n", name);
    }
}

/* Test 1: five equal periods -> median equals that period */
static void test_equal_periods(void)
{
    uint32_t p[5] = {168000U, 168000U, 168000U, 168000U, 168000U};
    uint32_t med = rpm_median5(p);
    check("five equal periods -> P", med == 168000U);
}

/* Test 2: one 0.5P and one 2P among five -> median is P */
/*
 * Periods: P, P, 0.5P, P, 2P
 * Sorted:  0.5P, P, P, P, 2P
 * Median:  P
 */
static void test_one_half_and_one_double(void)
{
    uint32_t P = 168000U;
    uint32_t p[5] = {P, P, P / 2U, P, P * 2U};
    uint32_t med = rpm_median5(p);
    check("one 0.5P and one 2P -> P", med == P);
}

/* Test 3: one missed edge (2P) alone among four P -> median is P */
/*
 * Periods: P, P, P, P, 2P
 * Sorted:  P, P, P, P, 2P
 * Median:  P
 */
static void test_missed_edge_alone(void)
{
    uint32_t P = 168000U;
    uint32_t p[5] = {P, P, P, P, P * 2U};
    uint32_t med = rpm_median5(p);
    check("one missed edge (2P) alone -> P", med == P);
}

/* Test 4: one extra edge (0.5P) alone among four P -> median is P */
/*
 * Periods: P, P, P, P, 0.5P
 * Sorted:  0.5P, P, P, P, P
 * Median:  P
 */
static void test_extra_edge_alone(void)
{
    uint32_t P = 168000U;
    uint32_t p[5] = {P, P, P, P, P / 2U};
    uint32_t med = rpm_median5(p);
    check("one extra edge (0.5P) alone -> P", med == P);
}

/* Test 5: sorted input unchanged */
static void test_sorted_input(void)
{
    uint32_t p[5] = {100000U, 120000U, 140000U, 160000U, 180000U};
    uint32_t med = rpm_median5(p);
    check("sorted input unchanged -> 140000", med == 140000U);
}

int main(void)
{
    test_equal_periods();
    test_one_half_and_one_double();
    test_missed_edge_alone();
    test_extra_edge_alone();
    test_sorted_input();

    printf("Results: %d passed, %d failed\n", pass_count, fail_count);
    return fail_count > 0 ? 1 : 0;
}