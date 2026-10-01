#ifndef SIM_STUB_GLOBAL_DECLARE_H
#define SIM_STUB_GLOBAL_DECLARE_H

#define   value_limit(x,small,big)   do { if((x)<(small)) (x)=(small); else if((x)>(big)) (x)=(big); } while(0)
#define ABS(x) ((x) > 0 ? (x) : -(x))

#endif
