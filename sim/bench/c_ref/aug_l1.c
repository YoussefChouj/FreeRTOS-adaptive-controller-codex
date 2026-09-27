/*
 * aug_l1.c -- L1 adaptive augmentation (float32, C89).
 *
 * All logic is in the header (static inline).
 * This translation unit exists so that a non-inlined build is possible
 * and so gcc -shared can produce a .so from it directly.
 */
#include "aug_l1.h"
