#ifndef _GlobalUse_Basic_Function_H_
#define _GlobalUse_Basic_Function_H_

#include "data_types.h"
#include "global_declare.h"

#define ABS(x) ( (x)>0?(x):-(x) )

FP32 Clip(FP32 siValue, FP32 siMin, FP32 siMax);
SINT32 Absolute_value(FP32 Number);
SINT32 Sign_Judge(FP32 fp_Judge_Number);
#endif
