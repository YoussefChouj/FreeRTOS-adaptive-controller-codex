#ifndef MRAC_VARIANT_H
#define MRAC_VARIANT_H

#define MRAC_VARIANT_STRUCT6       0
#define MRAC_VARIANT_STRUCT6_RBF12 1    /* V3: STRUCT6 + 4x3 Gaussian grid on (rate, angle), runtime rbf_on */
#define MRAC_VARIANT_MULTI         2    /* FW-B: STRUCT6 + 24 ext slots; runtime cfg basis picks the sim set */

/* FW-B basis ids (cfg->basis, pitch/roll). Sets from sim/adaptive_compare/sim_core.py features(). */
#define MRAC_BASIS_S6      0    /* the flown law, no ext slots */
#define MRAC_BASIS_S10     1    /* S6 + sin(angle), |rate|*u, u*|u|, accel */
#define MRAC_BASIS_RBF6    2    /* 3x2 Gaussian grid + u_nom, xm */
#define MRAC_BASIS_RBF12   3    /* 4x3 grid + u_nom, xm */
#define MRAC_BASIS_RBF24   4    /* 6x4 grid + u_nom, xm */
#define MRAC_BASIS_S6RBF12 5    /* S6 + the V3 4x3 grid (rbf_rate_scale, rbf_ang_scale) */
#define MRAC_BASIS_COUNT   6

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
#elif MRAC_VARIANT == MRAC_VARIANT_MULTI
    #define MRAC_N_STRUCT 6
    #define MRAC_N_EXT 24
    #define MRAC_N_FEATURES 30
    #ifndef MRAC_CAPACITY
        #define MRAC_CAPACITY MRAC_N_FEATURES
    #endif
    #define MRAC_N_GROUPS 7
#else
    #error "Unknown MRAC_VARIANT"
#endif

/* Highest basis id the setter accepts: a default build refuses every basis but S6 (vp_active 0xEE). */
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
#define MRAC_BASIS_HI 5.0f
#else
#define MRAC_BASIS_HI 0.0f
#endif

/* Law variants (WP-27, docs/workflow-b/mrac-variants.md). Compiled in by default so the flight build
 * carries them; every runtime row defaults to OFF and then the outputs are bit-identical to the law
 * without them (python API/tests/run_mrac_equiv.py -> EQUIV OK). Set a switch to 0 to compile it out.
 *   MRAC_ENABLE_REFMODEL_V2    V1: per-axis ref type, command delay, normalized drive
 *   MRAC_ENABLE_SATAWARE       V2: leakage mu_sat*|u_def|*Theta (u_def from API/controller.c)
 *   MRAC_ENABLE_PERF_RECOVERY  kappa_pr*(Theta-Whatf)'Phi on u_ad, closed-loop ref model crm_ell
 *   MRAC_ENABLE_3L             3-layer layer 1: lam_ang * integral of e in the drive (L2/L3 not built)
 * WP-33, same rules:
 *   MRAC_ENABLE_SET_THEORETIC  ST: restricted-potential gain on the gradient (st_eps) + log barrier (st_bar)
 *   MRAC_ENABLE_LF_HIGHGAIN    LFHG: per-axis low-frequency learning (sigma_lf, gam_f) with gamma x lf_gain */
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
#ifndef MRAC_ENABLE_SET_THEORETIC
#define MRAC_ENABLE_SET_THEORETIC 1
#endif
#ifndef MRAC_ENABLE_LF_HIGHGAIN
#define MRAC_ENABLE_LF_HIGHGAIN 1
#endif

#endif // MRAC_VARIANT_H
