#ifndef MRAC_VARIANT_H
#define MRAC_VARIANT_H

#define MRAC_VARIANT_STRUCT6       0
#define MRAC_VARIANT_STRUCT6_RBF12 1    /* V3: STRUCT6 + 4x3 Gaussian grid on (rate, angle), runtime rbf_on */

#ifndef MRAC_VARIANT
#define MRAC_VARIANT MRAC_VARIANT_STRUCT6
#endif

#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6
    #define MRAC_N_STRUCT 6
    #define MRAC_N_FEATURES 6
    #ifndef MRAC_CAPACITY
        #define MRAC_CAPACITY MRAC_N_FEATURES
    #endif
    #define MRAC_N_GROUPS 6
#elif MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    #define MRAC_N_STRUCT 6
    #define MRAC_N_RBF 12
    #define MRAC_N_FEATURES 18
    #ifndef MRAC_CAPACITY
        #define MRAC_CAPACITY MRAC_N_FEATURES
    #endif
    #define MRAC_N_GROUPS 7
#else
    #error "Unknown MRAC_VARIANT"
#endif

/* Law variants (WP-27, docs/workflow-b/mrac-variants.md). Compiled in by default so the flight build
 * carries them; every runtime row defaults to OFF and then the outputs are bit-identical to the law
 * without them (python API/tests/run_mrac_equiv.py -> EQUIV OK). Set a switch to 0 to compile it out.
 *   MRAC_ENABLE_REFMODEL_V2    V1: per-axis ref type, command delay, normalized drive
 *   MRAC_ENABLE_SATAWARE       V2: leakage mu_sat*|u_def|*Theta (u_def from API/controller.c)
 *   MRAC_ENABLE_PERF_RECOVERY  kappa_pr*(Theta-Whatf)'Phi on u_ad, closed-loop ref model crm_ell
 *   MRAC_ENABLE_3L             3-layer layer 1: lam_ang * integral of e in the drive (L2/L3 not built) */
#ifndef MRAC_ENABLE_REFMODEL_V2
#define MRAC_ENABLE_REFMODEL_V2 1
#endif
#ifndef MRAC_ENABLE_SATAWARE
#define MRAC_ENABLE_SATAWARE 1
#endif
#ifndef MRAC_ENABLE_PERF_RECOVERY
#define MRAC_ENABLE_PERF_RECOVERY 1
#endif
#ifndef MRAC_ENABLE_3L
#define MRAC_ENABLE_3L 1
#endif

#endif // MRAC_VARIANT_H
