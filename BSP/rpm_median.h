/**
 * @module  rpm_median.h
 * @subsystem  drivers
 * @owns  median-of-5 via insertion sort: pure function, no side effects.
 * @caution  ARMCC V5.06 C: locals at block top, no C99 constructs.
 *
 * Header-inline because new .c files are not in the Keil project (uvprojx is not edited);
 * rpm.c and the host test both include this header, so one body serves the ISR and the test.
 */
#ifndef RPM_MEDIAN_H
#define RPM_MEDIAN_H

#include <stdint.h>

/* Median of exactly five periods: insertion sort on a local copy, return a[2].
 * No division, no floats: bounded and safe in the ISR. */
static __inline uint32_t rpm_median5(const uint32_t p[5])
{
    uint32_t a[5];
    uint32_t tmp;
    uint8_t  i, j;

    for (i = 0; i < 5; i++) {
        a[i] = p[i];
    }
    for (i = 1; i < 5; i++) {
        tmp = a[i];
        for (j = i; j > 0 && a[j - 1] > tmp; j--) {
            a[j] = a[j - 1];
        }
        a[j] = tmp;
    }
    return a[2];
}

#endif /* RPM_MEDIAN_H */
