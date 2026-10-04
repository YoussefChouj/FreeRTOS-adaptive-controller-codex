/**
 * @module     Ano_OF.c
 * @subsystem  sensors
 * @owner      USART2_IRQHandler (stm32f4xx_it.c) feeds every received byte to AnoOF_GetOneByte().
 * @purpose    Byte-wise parser for the ANO optical-flow module. Frame on the wire:
 *               [0] 0xAA sync  [1] source address  [2] function id  [3] payload length n
 *               [4 .. 3+n] payload  [4+n] sum1  [5+n] sum2   (sum1 = running byte sum, sum2 = sum of the sum1 values)
 *             Function ids handled: 0x51 optical flow (mode 0 raw, 1 height-fused, 2 inertial-fused),
 *             0x34 height, 0x01 IMU, 0x04 attitude quaternion. See the ANO optical-flow module manual.
 * @inputs     UART bytes from the module.
 * @outputs    ano_of.* fields (StabilizerTask.c feeds of2_* and of_alt_cm to the EKF; send_data.c logs them),
 *             ano_of.of_update_cnt / alt_update_cnt bumped on each new flow / height frame.
 * @note       AnoOF_Check_State() has no caller today, so ano_of.link_sta and work_sta are never updated.
 */

#include "Ano_OF.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define ANOOF_SYNC            0xAA
#define ANOOF_OVERHEAD        6        /* sync, address, function id, length, sum1, sum2      */
#define ANOOF_TIMEOUT_TICKS   500      /* AnoOF_Check_State calls without a frame -> stale    */

#define ANOOF_FN_FLOW         0X51     /* optical flow; payload[0] selects the mode below     */
#define ANOOF_FN_ALT          0X34     /* height                                              */
#define ANOOF_FN_IMU          0X01     /* raw accelerometer and gyro                          */
#define ANOOF_FN_ATT          0X04     /* attitude quaternion                                 */

#define ANOOF_FLOW_RAW        0        /* raw flow, s8 dx/dy                                  */
#define ANOOF_FLOW_HEIGHT     1        /* height-fused flow                                   */
#define ANOOF_FLOW_INERTIAL   2        /* inertial-fused flow with drift-fixed and integral   */

#define ANOOF_QUAT_SCALE      0.0001f  /* quaternion LSB                                      */

/* ------------------------------------------------------------------
 * Public state
 * ------------------------------------------------------------------ */

_ano_of_st ano_of;

/* ------------------------------------------------------------------
 * Private state
 * ------------------------------------------------------------------ */

static uint8_t _datatemp[50];       /* frame assembly buffer                                     */
static float check_time_ms[3];      /* ticks since the last: [0] any frame, [1] flow, [2] height */

static void AnoOF_DataAnl(uint8_t *data, uint8_t len);

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* Link and data freshness. Meant to be called at a fixed rate (dT_s is unused); a source counts as stale once
   ANOOF_TIMEOUT_TICKS calls pass without its frame. */
void AnoOF_Check_State(float dT_s)
{
	u8 tmp[2];
	/* any frame */
	if (check_time_ms[0] < ANOOF_TIMEOUT_TICKS)
	{
		check_time_ms[0]++;
		ano_of.link_sta = 1;
	}
	else
	{
		ano_of.link_sta = 0;
	}
	/* height-fused flow */
	if (check_time_ms[1] < ANOOF_TIMEOUT_TICKS)
	{
		check_time_ms[1]++;
		tmp[0] = 1;
	}
	else
	{
		tmp[0] = 0;
	}
	/* height */
	if (check_time_ms[2] < ANOOF_TIMEOUT_TICKS)
	{
		check_time_ms[2]++;
		tmp[1] = 1;
	}
	else
	{
		tmp[1] = 0;
	}
	/* working only when both flow and height are fresh */
	if (tmp[0] && tmp[1])
	{
		ano_of.work_sta = 1;
	}
	else
	{
		ano_of.work_sta = 0;
	}
}

/* Feed one received byte. When a whole frame has arrived it is checked and decoded by AnoOF_DataAnl(). */
void AnoOF_GetOneByte(uint8_t data)
{
	static u8 _data_len = 0, _data_cnt = 0;
	static u8 rxstate = 0;

	if (rxstate == 0 && data == ANOOF_SYNC)
	{
		rxstate = 1;
		_datatemp[0] = data;
	}
	else if (rxstate == 1 )   /* source address: any accepted */
	{
		rxstate = 2;
		_datatemp[1] = data;
	}
	else if (rxstate == 2)    /* function id */
	{
		rxstate = 3;
		_datatemp[2] = data;
	}
	else if (rxstate == 3 && data <= (sizeof(_datatemp) - ANOOF_OVERHEAD))   /* payload length, must fit */
	{
		rxstate = 4;
		_datatemp[3] = data;
		_data_len = data;
		_data_cnt = 0;
	}
	else if (rxstate == 4 && _data_len > 0)   /* payload */
	{
		_data_len--;
		_datatemp[4 + _data_cnt++] = data;
		if (_data_len == 0)
			rxstate = 5;
	}
	else if (rxstate == 5)    /* sum1 */
	{
		rxstate = 6;
		_datatemp[4 + _data_cnt++] = data;
	}
	else if (rxstate == 6)    /* sum2: frame complete */
	{
		rxstate = 0;
		_datatemp[4 + _data_cnt] = data;
		check_time_ms[0] = 0;
		AnoOF_DataAnl(_datatemp, _data_cnt + 5);
	}
	else
	{
		rxstate = 0;
	}
}

/* ------------------------------------------------------------------
 * Private helpers
 * ------------------------------------------------------------------ */

/* Check the length and both sums of one frame, then copy its fields into ano_of. Multi-byte fields are
   little-endian and read in place (the Cortex-M4 allows unaligned halfword loads). */
static void AnoOF_DataAnl(uint8_t *data, uint8_t len)
{
	u8 check_sum1 = 0, check_sum2 = 0;
	if (*(data + 3) != (len - 6))   /* length byte must match the bytes received */
		return;
	for (u8 i = 0; i < len - 2; i++)
	{
		check_sum1 += *(data + i);
		check_sum2 += check_sum1;
	}
	if ((check_sum1 != *(data + len - 2)) || (check_sum2 != *(data + len - 1)))
		return;

	if (*(data + 2) == ANOOF_FN_FLOW)
	{
		if (*(data + 4) == ANOOF_FLOW_RAW)
		{
			ano_of.of0_sta = *(data + 5);
			ano_of.of0_dx = *(data + 6);
			ano_of.of0_dy = *(data + 7);
			ano_of.of_quality = *(data + 8);
		}
		else if (*(data + 4) == ANOOF_FLOW_HEIGHT)
		{
			ano_of.of1_sta = *(data + 5);
			ano_of.of1_dx = *((s16 *)(data + 6));
			ano_of.of1_dy = *((s16 *)(data + 8));
			ano_of.of_quality = *(data + 10);
			check_time_ms[1] = 0;
			ano_of.of_update_cnt++;
		}
		else if (*(data + 4) == ANOOF_FLOW_INERTIAL)
		{
			ano_of.of2_sta = *(data + 5);
			ano_of.of2_dx = *((s16 *)(data + 6));
			ano_of.of2_dy = *((s16 *)(data + 8));
			ano_of.of2_dx_fix = *((s16 *)(data + 10));
			ano_of.of2_dy_fix = *((s16 *)(data + 12));
			ano_of.intergral_x = *((s16 *)(data + 14));
			ano_of.intergral_y = *((s16 *)(data + 16));
			ano_of.of_quality = *(data + 18);
		}
	}
	else if (*(data + 2) == ANOOF_FN_ALT)
	{
		ano_of.of_alt_cm = ((u32)data[7]) | (((u32)data[8]) << 8) | (((u32)data[9]) << 16) | (((u32)data[10]) << 24);
		check_time_ms[2] = 0;
		ano_of.alt_update_cnt++;
	}
	else if (*(data + 2) == ANOOF_FN_IMU)
	{
		ano_of.acc_data_x = *((s16 *)(data + 4));
		ano_of.acc_data_y = *((s16 *)(data + 6));
		ano_of.acc_data_z = *((s16 *)(data + 8));
		ano_of.gyr_data_x = *((s16 *)(data + 10));
		ano_of.gyr_data_y = *((s16 *)(data + 12));
		ano_of.gyr_data_z = *((s16 *)(data + 14));
	}
	else if (*(data + 2) == ANOOF_FN_ATT)
	{
		ano_of.quaternion[0] = (*((s16 *)(data + 4))) * ANOOF_QUAT_SCALE;
		ano_of.quaternion[1] = (*((s16 *)(data + 6))) * ANOOF_QUAT_SCALE;
		ano_of.quaternion[2] = (*((s16 *)(data + 8))) * ANOOF_QUAT_SCALE;
		ano_of.quaternion[3] = (*((s16 *)(data + 10))) * ANOOF_QUAT_SCALE;
	}
}
