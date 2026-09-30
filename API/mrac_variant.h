#ifndef MRAC_VARIANT_H
#define MRAC_VARIANT_H

#define MRAC_VARIANT_STRUCT6 0

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
#else
    #error "Unknown MRAC_VARIANT"
#endif

#endif // MRAC_VARIANT_H
