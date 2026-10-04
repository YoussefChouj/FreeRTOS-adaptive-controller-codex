/**
 * @module     tf_mini_plus.c
 * @subsystem  sensors
 * @owner      nothing: no caller in the firmware (prototypes in tf_mini_plus.h).
 * @purpose    Parser for the Benewake TF-Mini Plus 9-byte serial frame:
 *               0x59 0x59 dist_L dist_H strength_L strength_H temp_L temp_H checksum
 *             checksum = low byte of the sum of the first 8 bytes; temperature [degC] = temp / 8 - 256.
 * @inputs     UART bytes (TF_Mini_Plus_Get_One_Byte) or a whole frame (TF_Mini_Plus_Update).
 * @outputs    TF_Mini_Plus_Distance (the old comments disagree on the unit: cm here, mm in the .h);
 *             TfMiniPlusFrameCnt / TfMiniPlusValidCnt frame counters.
 */

#include "tf_mini_plus.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define TFMP_SYNC         0x59   /* both frame-start bytes                      */
#define TFMP_PAYLOAD_LEN  6      /* distance, strength, temperature (2 B each)  */
#define TFMP_CSUM_IDX     8      /* checksum byte index in a whole frame        */

/* ------------------------------------------------------------------
 * Public state
 * ------------------------------------------------------------------ */

unsigned short TF_Mini_Plus_Distance;

unsigned char TfMiniPlusStateMachine=0;   /* 0, 1 sync; 2 payload; 3 checksum */
unsigned int TfMiniPlusFrameCnt=0,TfMiniPlusValidCnt=0;

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* Byte-wise frame parser for a UART RX interrupt. */
void TF_Mini_Plus_Get_One_Byte(unsigned char datax)
{
	static unsigned char tempsum,databuf[TFMP_PAYLOAD_LEN],i;
	if(TfMiniPlusStateMachine==0)   /* first sync byte */
	{
		if(datax==TFMP_SYNC)
		{
			TfMiniPlusFrameCnt++;
			tempsum=TFMP_SYNC;
			TfMiniPlusStateMachine++;
		}
	}
	else if(TfMiniPlusStateMachine==1)   /* second sync byte */
	{
		if(datax==TFMP_SYNC)
		{
			TfMiniPlusStateMachine++;
			tempsum+=datax;
			i=0;
		}
		else TfMiniPlusStateMachine=0;
	}
	else if(TfMiniPlusStateMachine==2)   /* payload */
	{
		if(i<TFMP_PAYLOAD_LEN)
		{
			databuf[i++]=datax;
			tempsum+=datax;
			if(i==TFMP_PAYLOAD_LEN)TfMiniPlusStateMachine++;
		}
		else TfMiniPlusStateMachine++;
	}
	else if(TfMiniPlusStateMachine==3)   /* checksum */
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

/* Parse one whole frame already in pBUF[0..TFMP_CSUM_IDX]. temp_sum is not initialised before the sum
   (behaviour kept; no caller in the firmware). */
void TF_Mini_Plus_Update(unsigned char* pBUF)
{
	unsigned char temp_sum,i;
	
	for(i=0;i<TFMP_CSUM_IDX;i++)
		temp_sum+=pBUF[i];
	
	if(temp_sum==pBUF[TFMP_CSUM_IDX])
		TF_Mini_Plus_Distance = pBUF[3]<<8|pBUF[2];
}
