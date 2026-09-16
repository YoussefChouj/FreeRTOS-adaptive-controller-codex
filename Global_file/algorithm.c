#include "algorithm.h"
#include "math.h"
#include "string.h"

float abs_fl(float value)
{
	if(value>0) return value;
	else if(value<0) return -value;
	else return 0;
}

void LpFilter(ST_LPF* lpf)   //用在陀螺仪那里
{                                                                   
	float fir_a = 1/(1 + lpf->off_freq * lpf->samp_tim); 
	lpf->preout = lpf->out;
  lpf->out = fir_a * lpf->preout + (1 - fir_a) * lpf->in;
}
