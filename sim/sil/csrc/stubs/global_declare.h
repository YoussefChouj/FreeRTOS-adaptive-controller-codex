/* SIL host stand-in for Global_file/global_declare.h: only what API/pid.c uses. */
#ifndef SIL_STUB_GLOBAL_DECLARE_H
#define SIL_STUB_GLOBAL_DECLARE_H

#define value_limit(x, small, big) do { if ((x) < (small)) (x) = (small); else if ((x) > (big)) (x) = (big); } while (0)
#define ABS(x) ((x) > 0 ? (x) : -(x))

#endif
