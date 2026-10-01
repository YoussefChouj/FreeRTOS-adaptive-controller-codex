/**
 * @module  rpm_median.c
 * @subsystem  drivers
 * @depends  rpm.h (rpm_median5 declaration), stm32f4xx.h (uint32_t)
 * @owns  median-of-5 via insertion sort — pure function, no side effects.
 * @caution  ARMCC V5.06 C: locals at block top, no C99 constructs.
 */

#include "rpm.h"

/**
 * @brief  Compute the median of exactly five 32-bit unsigned integers.
 *
 * Uses insertion sort on a local copy of the input array, then returns
 * the middle element (index 2 of the sorted copy).  No division, no
 * floats — safe for ARMCC V5.06 and the ISR context.
 *
 * @param  p  pointer to five uint32_t period values.
 * @return the median of p[0]..p[4].
 */
uint32_t rpm_median5(const uint32_t p[5])
{
    uint32_t a[5];
    uint32_t tmp;
    uint8_t  i, j;

    /* Copy input to local buffer (never mutate the caller's data). */
    for (i = 0; i < 5; i++) {
        a[i] = p[i];
    }

    /* Insertion sort — bounded to 5 elements, constant time. */
    for (i = 1; i < 5; i++) {
        tmp = a[i];
        for (j = i; j > 0 && a[j - 1] > tmp; j--) {
            a[j] = a[j - 1];
        }
        a[j] = tmp;
    }

    /* Median of 5 = element at index 2 (zero-based). */
    return a[2];
}