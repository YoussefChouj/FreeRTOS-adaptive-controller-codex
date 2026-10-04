/* RAM of the MRAC law variants, measured with sizeof on the host build (WP-33).
 *
 *   gcc -std=c99 -I API -I API/tests/stubs API/tests/mrac_sizeof.c -o mrac_sizeof && ./mrac_sizeof
 *
 * Fields are float (4 B on host and Cortex-M4); the structs hold only 4-byte members and uint8_t tails, so the host
 * layout matches the target. Per variant = its config fields x 4 axes (mrac_config_<ax>, CCM) + its state. */
#include <stdio.h>
#include <stdint.h>
#include "mrac.h"

#define F(x) ((unsigned)sizeof(((MRAC_AxisConfig_t *)0)->x))

int main(void)
{
    printf("MRAC_AxisConfig_t %u B, MRAC_AxisState_t %u B, x4 axes = %u B\n", (unsigned)sizeof(MRAC_AxisConfig_t),
           (unsigned)sizeof(MRAC_AxisState_t), 4U * (unsigned)(sizeof(MRAC_AxisConfig_t) + sizeof(MRAC_AxisState_t)));
    printf("PR   kappa_pr crm_ell (+ Whatf, in the base law)  %u B\n", 4U * (F(kappa_pr) + F(crm_ell)));
    printf("ST   st_eps st_phi_max st_bar                     %u B\n", 4U * (F(st_eps) + F(st_phi_max) + F(st_bar)));
    printf("LFHG lf_gain (sigma_lf gam_f Whatf: base law)     %u B\n", 4U * F(lf_gain));
    printf("mrac_var_id[AXES] (16-bit since WP-33)            %u B\n", (unsigned)(AXES * sizeof(uint16_t)));
    return 0;
}
