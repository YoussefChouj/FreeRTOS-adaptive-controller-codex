#include "GlobalUse_Basic_Function.h"

/*******************************************************************
函数名称：SINT32 fabs(FP32 Number)
函数功能：绝对值函数
********************************************************************/
SINT32 Absolute_value(FP32 Number)
{
   if(Number>=0) return Number;
	 else          return -Number;
}

/*--------------------------------------------------------------------------------------------------
函数名称：Clip()
函数功能：削波函数，去除超出最大值与最小值之间的值，代之以最大或最小值
--------------------------------------------------------------------------------------------------*/
FP32 Clip(FP32 fpValue, FP32 fpMin, FP32 fpMax)
{
	if(fpValue <= fpMin)
	{
		return fpMin;
	}
	else if(fpValue >= fpMax)
	{
		return fpMax;
	}
	else 
	{
		return fpValue;
	}
}

/*******************************************************************
函数名称：Sign_Judge(FP32 fp_Any_Number)
函数功能：判断正负
备    注：返回值为1和-1，来改变数的符号
********************************************************************/
SINT32 Sign_Judge(FP32 fp_Judge_Number)
{
	if(fp_Judge_Number >= 0)
	{
		return 1;
	}
	else 
	{
		return -1;
	}
}

