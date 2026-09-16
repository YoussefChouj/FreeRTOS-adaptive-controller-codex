#ifndef _ACCEL_CALIBRATION_H
#define _ACCEL_CALIBRATION_H

#include "bmi088_driver.h"
typedef struct
{
 float x;
 float y;
 float z;
}Acce_Unit;

extern Acce_Unit new_offset;
extern Acce_Unit new_scales;

#endif


