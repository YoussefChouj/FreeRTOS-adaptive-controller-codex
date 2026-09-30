#ifndef MRAC_VARIANT_H
#define MRAC_VARIANT_H

#define MRAC_VARIANT_STRUCT6 0
#define MRAC_VARIANT_RBF 1
#define MRAC_VARIANT_SINDY 2
#define MRAC_VARIANT_HYBRID_RBF 3
#define MRAC_VARIANT_HYBRID_SINDY 4
#define MRAC_VARIANT_3L_STRUCT 5
#define MRAC_VARIANT_3L_HYBRID_SINDY 6

#ifndef MRAC_VARIANT
#define MRAC_VARIANT MRAC_VARIANT_STRUCT6
#endif

#if MRAC_VARIANT >= MRAC_VARIANT_3L_STRUCT
    #ifndef MRAC_L2_MODE
        #define MRAC_L2_MODE 1
    #endif
    #ifndef MRAC_L3_MODE
        #define MRAC_L3_MODE 1
    #endif
#else
    #ifndef MRAC_L2_MODE
        #define MRAC_L2_MODE 0
    #endif
    #ifndef MRAC_L3_MODE
        #define MRAC_L3_MODE 0
    #endif
#endif

#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6
    #define MRAC_N_STRUCT 6
    #define MRAC_N_RBF 0
    #define MRAC_N_SINDY 0
    #define MRAC_N_GROUPS 6
#elif MRAC_VARIANT == MRAC_VARIANT_RBF
    #define MRAC_N_STRUCT 0
    #ifndef MRAC_N_RBF
        #define MRAC_N_RBF 7
    #endif
    #define MRAC_N_SINDY 0
    #define MRAC_N_GROUPS 8
#elif MRAC_VARIANT == MRAC_VARIANT_SINDY
    #define MRAC_N_STRUCT 0
    #define MRAC_N_RBF 0
    #define MRAC_N_SINDY 11
    #define MRAC_N_GROUPS 8
#elif MRAC_VARIANT == MRAC_VARIANT_HYBRID_RBF
    #define MRAC_N_STRUCT 6
    #ifndef MRAC_N_RBF
        #define MRAC_N_RBF 7
    #endif
    #define MRAC_N_SINDY 0
    #define MRAC_N_GROUPS 8
#elif MRAC_VARIANT == MRAC_VARIANT_HYBRID_SINDY
    #define MRAC_N_STRUCT 6
    #define MRAC_N_RBF 0
    #define MRAC_N_SINDY 6
    #define MRAC_N_GROUPS 8
#elif MRAC_VARIANT == MRAC_VARIANT_3L_STRUCT
    #define MRAC_N_STRUCT 6
    #define MRAC_N_RBF 0
    #define MRAC_N_SINDY 0
    #define MRAC_N_GROUPS 6
#elif MRAC_VARIANT == MRAC_VARIANT_3L_HYBRID_SINDY
    #define MRAC_N_STRUCT 6
    #define MRAC_N_RBF 0
    #define MRAC_N_SINDY 6
    #define MRAC_N_GROUPS 8
#else
    #error "Unknown MRAC_VARIANT"
#endif

#if MRAC_N_RBF > 0
typedef char MRAC_Assert_RBF_Limit[ (MRAC_N_RBF >= 1 && MRAC_N_RBF <= 16) ? 1 : -1 ];
#endif

#define MRAC_N_FEATURES (MRAC_N_STRUCT + MRAC_N_RBF + MRAC_N_SINDY)

#ifndef MRAC_CAPACITY
    #define MRAC_CAPACITY MRAC_N_FEATURES
#endif

#endif // MRAC_VARIANT_H
