#include "tf_mini_plus.h"
//59 59 distance_L  distance_H  strength_L  strength_H  Temp_L  Temp_H   checksum
//0   1   2              3          4           5          6        7        8
//tempature=Temp/8-256

unsigned short TF_Mini_Plus_Distance;//µ¥Î»£ºcm

unsigned char TfMiniPlusStateMachine=0;
unsigned int TfMiniPlusFrameCnt=0,TfMiniPlusValidCnt=0;

void TF_Mini_Plus_Get_One_Byte(unsigned char datax)
{
	static unsigned char tempsum,databuf[6],i;
	if(TfMiniPlusStateMachine==0)//  0x59
	{
		if(datax==0x59)
		{
			TfMiniPlusFrameCnt++;
			tempsum=0x59;
			TfMiniPlusStateMachine++;
		}
	}
	else if(TfMiniPlusStateMachine==1)//  0x59
	{
		if(datax==0x59)
		{
			TfMiniPlusStateMachine++;
			tempsum+=datax;
			i=0;
		}
		else TfMiniPlusStateMachine=0;
	}
	else if(TfMiniPlusStateMachine==2)// 6 bytes data
	{
		if(i<6)
		{
			databuf[i++]=datax;
			tempsum+=datax;
			if(i==6)TfMiniPlusStateMachine++;
		}
		else TfMiniPlusStateMachine++;
	}
	else if(TfMiniPlusStateMachine==3)// sum
	{
		if(tempsum==datax)
		{
			TF_Mini_Plus_Distance = databuf[1]<<8|databuf[0];
			TfMiniPlusValidCnt++;
		}
		TfMiniPlusStateMachine=0;
	}
	else
	{
		TfMiniPlusStateMachine=0;
	}
}

void TF_Mini_Plus_Update(unsigned char* pBUF)
{
	unsigned char temp_sum,i;
	
	for(i=0;i<8;i++)
		temp_sum+=pBUF[i];
	
	if(temp_sum==pBUF[8])
		TF_Mini_Plus_Distance = pBUF[3]<<8|pBUF[2];
}
